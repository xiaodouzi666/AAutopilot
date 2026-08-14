from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from a64pilot.agent.schema import SplitManifest

ROOT = Path(__file__).resolve().parents[1]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_split_v2_mechanically_replays_immutable_freeze() -> None:
    freeze = json.loads((ROOT / "demo/split-freeze-v2.json").read_text(encoding="utf-8"))
    split_path = ROOT / "demo/split.json"
    split = json.loads(split_path.read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in (ROOT / "demo/cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    categories = {case["case_id"]: case["category"] for case in cases}

    assert freeze["schema_version"] == "2.0"
    assert freeze["domain"] == "a64pilot-final-holdout-v2"
    assert freeze["seed"] == 20260813
    assert _sha256((ROOT / "demo/cases.jsonl").read_bytes()) == freeze["cases_sha256"]
    assert freeze["run6_id"] == "31758292648"
    assert freeze["category_quotas"] == {
        "simple": 6,
        "multi": 7,
        "noisy": 3,
        "ambiguous": 4,
    }

    observed_calibration = freeze["observed_v1_calibration_ids"]
    observed_test = freeze["observed_v1_test_ids"]
    observed = freeze["observed_ids"]
    eligible = freeze["eligible_v1_calibration"]
    assert observed == observed_calibration + observed_test
    assert len(observed) == len(set(observed)) == 24
    assert len(eligible) == 36
    assert all(set(row) == {"case_id", "category", "digest"} for row in eligible)

    eligible_ids = [row["case_id"] for row in eligible]
    assert len(eligible_ids) == len(set(eligible_ids)) == 36
    assert not set(observed) & set(eligible_ids)
    assert set(observed) | set(eligible_ids) == set(categories)

    old_split = {
        "schema_version": "1.0",
        "seed": freeze["seed"],
        "calibration": observed_calibration + eligible_ids,
        "test": observed_test,
    }
    old_split_bytes = (json.dumps(old_split, indent=2) + "\n").encode()
    assert _sha256(old_split_bytes) == freeze["old_split_sha256"]
    with pytest.raises(ValidationError, match="2.0"):
        SplitManifest.model_validate(old_split)

    by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in eligible:
        case_id = row["case_id"]
        assert row["category"] == categories[case_id]
        digest = _sha256(f"{freeze['domain']}|{freeze['seed']}|{case_id}".encode())
        assert row["digest"] == digest
        by_category[row["category"]].append((digest, case_id))

    selected_pairs: list[tuple[str, str]] = []
    for category, quota in freeze["category_quotas"].items():
        selected_pairs.extend(sorted(by_category[category])[:quota])
    replayed_test = [case_id for _, case_id in sorted(selected_pairs)]
    assert replayed_test == freeze["selected_final_test"] == split["test"]
    assert not set(replayed_test) & set(observed)
    assert Counter(categories[case_id] for case_id in replayed_test) == Counter(
        freeze["category_quotas"]
    )

    selected = set(replayed_test)
    replayed_calibration = (
        observed_calibration
        + [case_id for case_id in eligible_ids if case_id not in selected]
        + observed_test
    )
    assert replayed_calibration == split["calibration"]
    assert len(split["calibration"]) == 40
    assert len(split["test"]) == 20
    assert len(set(split["calibration"] + split["test"])) == 60
    assert _sha256(split_path.read_bytes()) == freeze["new_split_sha256"]

    declaration = freeze["selection_declaration"].casefold()
    assert "only case_id and category" in declaration
    for excluded_input in ("model outputs", "quality scores", "latencies", "private labels"):
        assert excluded_input in declaration
