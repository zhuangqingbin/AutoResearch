from __future__ import annotations

import json
from copy import deepcopy

import pandas as pd
import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.sector.brief import extract_terrain, render_deterministic_terrain
from autoresearch.sector.pack import _sector_pack_staging
from autoresearch.sector.reuse import reuse_stable_facts, stable_fact_snapshot
from autoresearch.sector.terrain import (
    FIELD_UNITS,
    classification_metadata,
    compare_terrain_candidates,
    enrich_pack,
    event_request,
    validate_event_supplement,
)
from tests.sector.test_pack import _mk_scan

CUTOFF = "2026-07-03T18:00:00+08:00"


def candidate(tmp_path, *, evidence=None):
    root = _mk_scan(tmp_path)
    if evidence is not None:
        (root / "sector_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False))
    return _sector_pack_staging("半导体", root, profile="deterministic-v1")


def marked_candidate(tmp_path):
    return candidate(
        tmp_path,
        evidence={
            "signals": {
                "半导体": {
                    "major_new_events": [
                        {
                            "id": "policy-1",
                            "detail": "政策文件新增要求待核",
                            "source_ref": "calendar.csv",
                        }
                    ]
                }
            }
        },
    )


def test_renderer_preserves_fields_units_actual_dates_and_single_contract(tmp_path):
    pack = candidate(
        tmp_path,
        evidence={
            "sources": {"L1_scored_full.csv": {"as_of": "2026-07-02", "provider": "tushare"}}
        },
    )
    text = render_deterministic_terrain(pack)
    assert text.count("## ") == 1 and "\n## 地形段\n" in text
    terrain = extract_terrain(text)
    for key, unit in FIELD_UNITS.items():
        assert f"- {key}:" in terrain
        assert pack["terrain"]["fields"][key]["unit"] == unit
    assert "median_pe: 60.0 倍" in terrain
    assert "main_pos_frac: 0.25 fraction" in terrain
    assert "数据日期: 2026-07-02" in terrain
    assert "来源日期与目标日不符:2026-07-02" in terrain
    assert "provider_industry" in terrain and "UNMAPPED" in terrain
    assert "申万一级" not in terrain
    assert "sector_healthy_top3" not in terrain


def test_no_date_in_source_is_unknown_even_if_mtime_or_directory_is_today(tmp_path):
    pack = candidate(tmp_path)
    assert pack["as_of"] == "2026-07-03"
    assert pack["terrain"]["fields"]["median_pe"]["as_of"] is None
    assert "来源日期未核" in render_deterministic_terrain(pack)


def test_csv_date_and_declared_date_conflict_requires_supplement(tmp_path):
    root = _mk_scan(tmp_path)
    frame = pd.read_csv(root / "L1_scored_full.csv")
    frame["trade_date"] = "20260702"
    frame.to_csv(root / "L1_scored_full.csv", index=False)
    pack = enrich_pack(
        _sector_pack_staging("半导体", root),
        root,
        evidence={"sources": {"L1_scored_full.csv": {"as_of": "2026-07-03"}}},
    )
    request = event_request(pack, max_queries=2, knowledge_cutoff=CUTOFF)
    assert request["needed"] and request["dispatch"]
    assert request["reasons"][0]["kind"] == "source_conflicts"
    with pytest.raises(ValueError, match="event request"):
        render_deterministic_terrain(pack)


def test_renderer_missing_values_are_unknown_and_never_read_directional_extras(tmp_path):
    pack = candidate(tmp_path)
    pack["terrain"]["fields"]["median_roe"]["value"] = None
    pack["calendar"] = None
    pack["sector_healthy_top3"] = "看多/买入"
    pack["readthrough"] = [{"symbol": "secret-not-admitted"}]
    text = render_deterministic_terrain(pack)
    assert "median_roe: 未核 %" in text
    assert "缺失不等于没有事件" in text
    assert "看多" not in text and "secret-not-admitted" not in text


def test_subindustry_cannot_claim_unverified_level_one_mapping():
    original = classification_metadata("半导体")
    original["target_mapping"] = {
        "system": "SW",
        "version": "2021",
        "level": 1,
        "code": "801080",
        "name": "电子",
        "mapping_version": "map-v1",
        "source_ref": "mapping.json@abc",
    }
    with pytest.raises(ValueError, match="ambiguous"):
        classification_metadata("半导体", original)
    original.update(coverage=1.0, ambiguity="NONE")
    mapped = classification_metadata("半导体", original)
    assert mapped["original_name"] == "半导体" and mapped["target_mapping"]["name"] == "电子"
    original["target_mapping"]["level"] = 2
    with pytest.raises(ValueError, match="level-one"):
        classification_metadata("半导体", original)


def supplement(request):
    return {
        "schema_version": 1,
        "pack_sha256": request["pack_sha256"],
        "unresolved_reason_ids": [],
        "events": [
            {
                "reason_id": "policy-1",
                "claim": "部门公布政策文件。",
                "source_url": "https://example.test/policy",
                "published_at": "2026-07-03T10:00:00+08:00",
                "available_at": "2026-07-03T11:00:00+08:00",
                "source_observation_id": "obs-1",
                "source_text_sha256": sha256_bytes("部门公布政策文件。".encode()),
                "quote": "部门公布政策文件。",
            }
        ],
    }


def bound(event, request):
    # Owner-side deterministic raw-text verification fixture; never trust model verdicts.
    assert event["source_text_sha256"] == sha256_bytes("部门公布政策文件。".encode())
    return {"verdict": "PASS"}


def test_events_only_when_flagged_and_budget_zero_records_missing_fact(tmp_path):
    pack = candidate(tmp_path)
    request = event_request(pack, max_queries=2, knowledge_cutoff=CUTOFF)
    assert not request["needed"] and not request["dispatch"]
    pack = marked_candidate(tmp_path / "flagged")
    request = event_request(pack, max_queries=0, knowledge_cutoff=CUTOFF)
    assert request["needed"] and not request["dispatch"]
    assert "事件缺口: policy-1 未核" in render_deterministic_terrain(pack, request=request)


def test_event_facts_require_owner_binding_and_cannot_override_numbers(tmp_path):
    pack = marked_candidate(tmp_path)
    request = event_request(pack, max_queries=2, knowledge_cutoff=CUTOFF)
    payload = supplement(request)
    with pytest.raises(ValueError, match="source binding"):
        validate_event_supplement(request, payload)
    text = render_deterministic_terrain(pack, request=request, supplement=payload, bind_claim=bound)
    assert "median_pe: 60.0 倍" in text and "部门公布政策文件" in text
    payload["events"][0]["median_pe"] = 7
    with pytest.raises(ValueError, match="overrides forbidden"):
        validate_event_supplement(request, payload, bind_claim=bound)


@pytest.mark.parametrize(
    "mutation", ["future", "wrong_pack", "missing_reason", "direction", "budget"]
)
def test_event_contract_rejects_inadmissible_facts(tmp_path, mutation):
    pack = marked_candidate(tmp_path)
    request = event_request(pack, max_queries=1, knowledge_cutoff=CUTOFF)
    payload = supplement(request)
    if mutation == "future":
        payload["events"][0]["available_at"] = "2026-07-04T11:00:00+08:00"
    elif mutation == "wrong_pack":
        payload["pack_sha256"] = "0" * 64
    elif mutation == "missing_reason":
        payload["events"] = []
    elif mutation == "direction":
        payload["events"][0]["claim"] = "建议买入本行业"
    else:
        payload["events"].append(deepcopy(payload["events"][0]))
    with pytest.raises(ValueError):
        validate_event_supplement(request, payload, bind_claim=bound)


def stable_fact():
    return {
        "kind": "policy_rule",
        "claim": "部门公布政策文件。",
        "as_of": "2026-07-01",
        "valid_until": "2026-07-10",
        "available_at": "2026-07-01T11:00:00+08:00",
        "source_url": "https://example.test/policy",
        "source_text_sha256": sha256_bytes("部门公布政策文件。".encode()),
        "source_observation_id": "obs-1",
    }


def test_stable_reuse_retains_original_date_hash_and_rebuilds_current_numbers(tmp_path):
    pack = candidate(tmp_path)
    previous = stable_fact_snapshot(pack, [stable_fact()])
    previous["as_of"] = "2026-07-02"
    result = reuse_stable_facts(
        pack, previous, ttl_days=5, knowledge_cutoff=CUTOFF, bind_claim=bound
    )
    assert result["reused"] and result["numeric_rebuilt"]
    assert result["stable_facts"][0]["as_of"] == "2026-07-01"
    assert result["previous_pack_sha256"] == previous["pack_sha256"]
    text = render_deterministic_terrain(pack, stable_facts=result["stable_facts"])
    assert "原始日期: 2026-07-01" in text
    assert "median_pe: 60.0 倍" in text
    assert "body" not in result


@pytest.mark.parametrize("changed", ["market", "financial", "event", "correction", "mapping"])
def test_any_input_fingerprint_change_invalidates_cached_facts(tmp_path, changed):
    pack = candidate(tmp_path)
    previous = stable_fact_snapshot(pack, [stable_fact()])
    previous["fingerprints"][changed] = "0" * 64
    result = reuse_stable_facts(
        pack, previous, ttl_days=5, knowledge_cutoff=CUTOFF, bind_claim=bound
    )
    assert not result["reused"] and changed.upper() + "_CHANGED" in result["invalidations"]


@pytest.mark.parametrize("defect", ["expired", "future", "unbound", "ttl"])
def test_stable_fact_source_lifetimes_rechecked(tmp_path, defect):
    pack = candidate(tmp_path)
    previous = stable_fact_snapshot(pack, [stable_fact()])
    kwargs = {"ttl_days": 5, "knowledge_cutoff": CUTOFF, "bind_claim": bound}
    if defect == "expired":
        previous["stable_facts"][0]["valid_until"] = "2026-07-02"
    elif defect == "future":
        previous["stable_facts"][0]["available_at"] = "2026-07-04T10:00:00+08:00"
    elif defect == "ttl":
        previous["as_of"] = "2026-06-01"
    else:
        kwargs.pop("bind_claim")
    assert not reuse_stable_facts(pack, previous, **kwargs)["reused"]


def test_comparison_preserves_raw_text_and_requires_quality_before_cost(tmp_path):
    pack = candidate(tmp_path)
    checks = {
        name: {"verdict": "PASS", "evidence_refs": [f"review/{name}.json"]}
        for name in ("field_coverage", "event_evidence", "injection_scope", "actual_measurement")
    }
    result = compare_terrain_candidates(
        pack,
        deepcopy(pack),
        baseline_text="原始判断",
        candidate_text="变更判断",
        quality_checks=checks,
    )
    assert result["verdict"] == "PASS" and result["research_diffs"][0]["baseline"] == "原始判断"
    changed = deepcopy(pack)
    changed["terrain"]["fields"]["main_pos_frac"]["unit"] = "%"
    assert (
        compare_terrain_candidates(
            pack, changed, baseline_text="a", candidate_text="b", quality_checks=checks
        )["verdict"]
        == "FAIL"
    )
    checks.pop("actual_measurement")
    assert (
        compare_terrain_candidates(
            pack, pack, baseline_text="a", candidate_text="b", quality_checks=checks
        )["verdict"]
        == "INCOMPLETE"
    )
