import json

import pytest

from autoresearch.common.atomic import sha256_file
from autoresearch.research import stage_adapters as sa


def frozen(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": sha256_file(path)}


def identity(profile):
    return {
        "run_id": profile,
        "profile": profile,
        "engine": "codex",
        "input_hash": "a" * 64,
        "code_sha": "b" * 40,
        "prompt_hashes": {"registry": "c" * 64},
        "config_hashes": {"config": "d" * 64},
    }


def test_ensemble_uses_post_verify_and_retains_missing_required_reviews(tmp_path):
    artifact = frozen(
        tmp_path,
        "ensemble.json",
        {
            "identity": identity("ensemble"),
            "rows": [
                {
                    "date": "2026-09-01",
                    "code": "600000",
                    "source_rating": "Buy",
                    "post_verify_rating": "Hold",
                    "final_rating": "Hold",
                    "review_required": True,
                    "review_status": "COMPLETE",
                    "value": 0.01,
                },
                {
                    "date": "2026-09-01",
                    "code": "600001",
                    "source_rating": "Buy",
                    "post_verify_rating": "Buy",
                    "final_rating": None,
                    "review_required": True,
                    "review_status": "MISSING",
                    "value": -0.02,
                },
            ],
        },
    )
    result = sa.ensemble_pairs(artifact, selected_ratings=["Buy"])
    assert result["rows"][0]["baseline"] is False
    assert result["rows"][1]["refined"] is None
    assert result["coverage"]["unknown_rows"] == 1
    assert result["selection_semantics"] == "RATING_SELECTION_NOT_FINAL_BUY"


def test_b3_does_not_use_initial_judgment_as_one_stage(tmp_path):
    two = frozen(
        tmp_path,
        "two.json",
        {
            "identity": identity("two_stage"),
            "rows": [
                {
                    "date": "2026-09-01",
                    "code": "600000",
                    "initial_rating": "Buy",
                    "final_rating": "Hold",
                    "value": 0.01,
                }
            ],
        },
    )
    result = sa.b3_pairs(None, two, selected_ratings=["Buy"])
    assert result["rows"][0]["baseline"] is None
    assert result["coverage"]["status"] == "UNKNOWN"
    assert result["additional_evidence_changes"][0]["changed"] is True
    one = frozen(
        tmp_path,
        "one.json",
        {
            "identity": identity("one_stage"),
            "rows": [{"date": "2026-09-01", "code": "600000", "final_rating": "Buy", "value": 0.01}],
        },
    )
    result = sa.b3_pairs(one, two, selected_ratings=["Buy"])
    assert result["rows"][0]["baseline"] is True and result["rows"][0]["refined"] is False


def decision(pool="finalists", tiering=False):
    rows = []
    for code, faces in [("600000", [0.9, 0.1, 0.2, 0.4]), ("600001", [0.8, 0.9, 0.9, 0.4])]:
        rows.append(
            {
                "code": code,
                "faces": dict(zip(sa.FACES, faces, strict=True)),
                "relative_decision_score": round(sum(faces) / 4, 6),
                "eligible": True,
                "hard_gate": {"no_redflag": True},
                "pinned": False,
                "in_pool": True,
                "card_context": {"entry_stance": "ALLOWED" if code == "600000" else "CONDITIONAL"},
            }
        )
    codes = ["600000", "600001"] if pool == "composite" else ["600001", "600000"]
    names = (
        ["target_align", "amount", "code"]
        if pool == "composite"
        else ["relative_decision_score", "target_align", "amount", "code"]
    )
    values = {
        r["code"]: (
            [r["faces"]["target_align"], 5, r["code"]]
            if pool == "composite"
            else [r["relative_decision_score"], r["faces"]["target_align"], 5, r["code"]]
        )
        for r in rows
    }
    winner = "600000" if tiering else codes[0]
    buy = {"code": winner, "basis": "card_backed" if tiering else "relative", "rank": 1}
    if tiering:
        buy["tier"] = "A"
    return {
        "date": "2026-09-01",
        "pool": pool,
        "exclude_pinned": True,
        "tiering": tiering,
        "rule_params": {"max_buys": 1},
        "candidates": rows,
        "buys": [buy],
        "selection": {
            "codes": codes,
            "sort_keys": {
                "names": names,
                "directions": ["desc"] * (len(names) - 1) + ["asc"],
                "values": values,
            },
        },
    }


@pytest.mark.parametrize("pool", ["finalists", "composite"])
def test_ablation_replays_baseline_and_changes_only_registered_face(tmp_path, pool):
    doc = decision(pool)
    artifact = frozen(tmp_path, "decision.json", doc)
    result = sa.e6_ablation(artifact, remove_face="recall_strength")
    assert result["baseline"]["buys"] == doc["buys"]
    assert result["factor_used_in_selection"] is (pool == "finalists")
    if pool == "composite":
        assert result["unchanged"] is True
    assert json.loads((tmp_path / "decision.json").read_text()) == doc


def test_ablation_preserves_tiering_and_refuses_baseline_mismatch(tmp_path):
    doc = decision(tiering=True)
    artifact = frozen(tmp_path, "decision.json", doc)
    assert sa.e6_ablation(artifact, remove_face="evidence")["refined"]["buys"][0]["tier"] == "A"
    doc["selection"]["codes"].reverse()
    bad = frozen(tmp_path, "bad.json", doc)
    with pytest.raises(ValueError, match="baseline"):
        sa.e6_ablation(bad, remove_face="evidence")


def test_quality_violations_cannot_promote_and_coverage_keeps_rejected():
    rows = [
        {
            "date": "2026-09-01", "code": "600000", "baseline": True, "refined": False, "value": None, "sector": "A"
        },
        {
            "date": "2026-09-01", "code": "600001", "baseline": False, "refined": False, "value": 0.1, "sector": "A"
        },
    ]
    report = sa.coverage(rows)
    assert (
        report["security_days"] == 2
        and report["rejected_rows"] == 2
        and report["unselected_rows"] == 1
    )
    assert report["industry_hhi"] == 1
    gate = sa.quality_gate(
        {
            "software_errors": 0,
            "future_information": 1,
            "boundary_violations": 0,
            "required_evidence_missing": 0,
            "incorrect_publications": 0,
        }
    )
    assert gate["promotion_allowed"] is False


def test_adapter_unknown_member_blocks_whole_day(tmp_path):
    result = {
        "rows": [
            {"date": "2026-09-01", "code": "600000", "baseline": True, "refined": True, "value": 0.2},
            {"date": "2026-09-01", "code": "600001", "baseline": True, "refined": None, "value": -0.5},
        ],
        "coverage": {},
    }
    output = sa.evaluate_adapter(
        result, stage="ensemble", evidence_root=tmp_path, seed=7, n_boot=20
    )
    assert output["daily"][0]["status"] == "UNKNOWN_SELECTION"
    assert output["statistics"]["n_complete"] == 0


def test_b3_input_identity_mismatch_refused(tmp_path):
    a = {"identity": identity("one_stage"), "rows": []}
    b = {"identity": identity("two_stage"), "rows": []}
    b["identity"]["input_hash"] = "f" * 64
    with pytest.raises(ValueError, match="input_hash"):
        sa.b3_pairs(frozen(tmp_path, "a", a), frozen(tmp_path, "b", b), selected_ratings=["Buy"])


def test_e6_pairs_preserve_unselected_and_missing_outcomes(tmp_path):
    reference = frozen(tmp_path, "decision.json", decision())
    outcomes = frozen(
        tmp_path,
        "labels.json",
        {"rows": [{"date": "2026-09-01", "code": "600000", "value": 0.01, "status": "MATURE"}]},
    )
    result = sa.e6_pairs(reference, outcomes, remove_face="evidence")
    assert len(result["rows"]) == 2
    assert result["coverage"]["missing_outcomes"] == 1
    assert result["rows"][1]["value"] is None


def test_forward_cli_parser_and_adapter_cli_keep_failed_attempt(tmp_path):
    output = tmp_path / "attempt.json"
    ref = tmp_path / "reference.json"
    ref.write_text(json.dumps({"path": str(tmp_path / "missing"), "sha256": "f" * 64}))
    assert (
        sa.main(
            ["e6", "--reference", str(ref), "--remove-face", "evidence", "--output", str(output)]
        )
        == 2
    )
    assert json.loads(output.read_text())["status"] == "FAILED"
    with pytest.raises(FileExistsError):
        sa.main(
            ["e6", "--reference", str(ref), "--remove-face", "evidence", "--output", str(output)]
        )


@pytest.mark.parametrize("rating", [None, "—", "UNKNOWN", "Buuy", ""])
def test_unknown_rating_is_not_a_rejection(tmp_path, rating):
    artifact = frozen(
        tmp_path,
        "ratings.json",
        {
            "identity": identity("ensemble"),
            "rows": [
                {
                    "date": "2026-09-01",
                    "code": "600000",
                    "post_verify_rating": rating,
                    "final_rating": rating,
                    "review_required": True,
                    "review_status": "COMPLETE",
                    "value": 0.1,
                }
            ],
        },
    )
    result = sa.ensemble_pairs(artifact, selected_ratings=["Buy"])
    assert result["rows"][0]["baseline"] is None
    assert result["rows"][0]["refined"] is None
    assert result["coverage"]["unknown_rows"] == 1


def test_selection_predicate_rejects_unknown_rating(tmp_path):
    artifact = frozen(tmp_path, "ratings.json", {"identity": identity("ensemble"), "rows": []})
    with pytest.raises(ValueError, match="rating"):
        sa.ensemble_pairs(artifact, selected_ratings=["UNKNOWN"])


def test_b3_cannot_relabel_one_real_run_as_two_independent_arms(tmp_path):
    left = {"identity": identity("one_stage"), "rows": []}
    right = {"identity": identity("two_stage"), "rows": []}
    right["identity"]["run_id"] = left["identity"]["run_id"]
    one = frozen(tmp_path, "same-run-one.json", left)
    two = frozen(tmp_path, "same-run-two.json", right)
    with pytest.raises(ValueError, match="independent"):
        sa.b3_pairs(one, two, selected_ratings=["Buy"])
