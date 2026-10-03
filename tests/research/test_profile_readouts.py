import importlib


def module():
    return importlib.import_module("autoresearch.research.profile_readouts")


def test_sector_denominator_includes_every_l3_industry():
    result = module().sector_coverage(["银行", "科技"], {
        "银行": {"numeric_rebuilt": True, "event_status": "NOT_TRIGGERED", "direction_free": True}}, {})
    assert result["required_sectors"] == ["科技", "银行"]
    assert result["candidate"]["missing_sectors"] == ["科技", "银行"]
    assert result["baseline"]["event_not_triggered"] == ["银行"]
    assert result["comparable"] is False


def test_stable_cache_miss_reasons_not_assumed_savings():
    result = module().stable_reuse_readout([
        {"reused": False, "invalidations": ["MARKET_CHANGED"]},
        {"reused": False, "invalidations": ["MARKET_CHANGED", "EVENT_CHANGED"]},
        {"reused": True, "invalidations": []}])
    assert result["market_only_invalidations"] == 1
    assert result["price_only_invalidations"] is None
    assert result["fact_level_reuse_adoption"] == "REQUIRES_FACT_DEPENDENCY_EVIDENCE"


def test_stock_stage_missing_values_are_unknown():
    result = module().stock_stage_value([])
    assert len(result["stages"]) == 7
    assert all(row["tokens"] is None for row in result["stages"])
    assert result["grouped3_development"] == "DEFER_PENDING_BASELINE_EVIDENCE"


def test_duplicate_scheduling_snapshot_not_double_counted():
    row = {"segment_id": "a", "elapsed_seconds": 5, "slot_idle_seconds": 3,
           "deterministic_lane_busy_seconds": 2, "ready_queue_wait": {}}
    result = module().scheduling_readout([row, row], tasks=[])
    assert result["segments"] == 1
    assert result["observed_segment_seconds"] == 5
    assert result["net_wall_seconds"] is None
    assert result["critical_path_seconds"] is None


def test_critical_path_uses_dependencies_not_sum_of_parallel_tasks():
    tasks = [{"task_id": "a", "dependencies": [], "observed_duration_seconds": 3},
             {"task_id": "b", "dependencies": [], "observed_duration_seconds": 4},
             {"task_id": "c", "dependencies": ["a", "b"], "observed_duration_seconds": 2}]
    assert module().scheduling_readout([], tasks=tasks)["critical_path_seconds"] == 6


def test_macro_comparison_reads_exact_refs_and_all_21_products(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws
    from autoresearch.research.evidence_refs import write_derived
    from autoresearch.session_agent.workflows.macro import required_macro_products
    from autoresearch.session_agent.workflows.macro_groups import QUALITY_CHECKS
    monkeypatch.chdir(tmp_path)
    data = write_derived(ws.context_root() / "data.json", {"fixture": True})
    common = {"engine": ws.ENGINE, "settings": {"observed_model": "synthetic"}, "optional_products": [],
              "inputs": dict.fromkeys(("raw_pack", "intel", "frame", "cutoff"), data),
              "products": dict.fromkeys(required_macro_products(), data)}
    baseline = write_derived(ws.context_root() / "base.json", dict(common, profile="serial21", run_id="20261001T000000000001Z"))
    candidate = write_derived(ws.context_root() / "candidate.json", dict(common, profile="six_groups_v1", run_id="20261001T000000000002Z"))
    quality = {key: write_derived(ws.context_root() / f"{key}.json", {"baseline_run_id": "20261001T000000000001Z",
        "candidate_run_id": "20261001T000000000002Z", "verdict": "INCOMPLETE",
        "baseline_sha256": baseline["sha256"], "candidate_sha256": candidate["sha256"]}) for key in QUALITY_CHECKS}
    result = module().compare_macro_refs(baseline, candidate, quality_refs=quality)
    assert len(result["required_products"]) == 21
    assert result["cost_savings"] is None
    changed = write_derived(ws.context_root() / "changed.json", dict(common, profile="six_groups_v1",
        run_id="20261001T000000000002Z", settings={"observed_model": "changed"}))
    import pytest
    with pytest.raises(ValueError, match="bound"):
        module().compare_macro_refs(baseline, changed, quality_refs=quality)


def test_macro_comparison_legacy_exports_share_lower_layer_implementation():
    from autoresearch.contracts import session_comparison
    from autoresearch.macro import grouped_products
    from autoresearch.session_agent import evaluation
    from autoresearch.session_agent.workflows import macro, macro_groups

    assert evaluation.build_comparison is session_comparison.build_comparison
    assert evaluation.validate_comparison is session_comparison.validate_comparison
    assert macro_groups.compare_macro_candidate is grouped_products.compare_macro_candidate
    assert macro_groups.grouped_products is grouped_products.grouped_products
    assert macro.required_macro_products is grouped_products.required_macro_products
