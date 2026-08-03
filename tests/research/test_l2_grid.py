"""L2 参数网格单测 —— §3.4 O4:**探索,不裁决**。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.research import l2_grid, replay

# 守卫是真会拦人的(集中度 ≤0.35、lane ≥4)。合成日必须自带足够的行业/lane 多样性,
# 否则每个格点都因为"fixture 太薄"被判 GUARD_BREACH —— 那是在测 fixture,不是测网格。
_INDUSTRIES = ["电子", "白酒", "医药", "银行", "机械", "化工"]
_LANES = ["momentum", "value", "growth", "healthy", "reversal", "main_fund"]


def _day(root, date, *, l2_codes, winners, industries=None, lanes=None,
         filler=8):
    d = root / date
    (d / "retro").mkdir(parents=True, exist_ok=True)
    # filler 票只为把行业/lane 铺开,不参与赢家判定
    all_codes = list(l2_codes) + [f"3{i:05d}" for i in range(filler)]
    pd.DataFrame({"code": all_codes}).to_csv(d / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": all_codes}).to_csv(d / "L1_recall_top1000.csv", index=False)
    frame = pd.DataFrame({"code": all_codes})
    frame["industry"] = industries or [_INDUSTRIES[i % len(_INDUSTRIES)]
                                       for i in range(len(all_codes))]
    frame["recall_channels"] = lanes or [_LANES[i % len(_LANES)]
                                         for i in range(len(all_codes))]
    frame["selection_reason"] = ["merit"] * len(all_codes)
    frame.to_csv(d / "L2_gbdt_top200.csv", index=False)
    attr = [{"code": f"1{i:05d}", "fwd_2_oc": 0.0, "buyable": True, "tradable": True}
            for i in range(19)]
    attr += [{"code": c, "fwd_2_oc": 0.0, "buyable": True, "tradable": True}
             for c in all_codes if c.startswith("3")]
    attr += [{"code": c, "fwd_2_oc": 0.50, "buyable": True, "tradable": True}
             for c in winners]
    pd.DataFrame(attr).to_csv(d / "retro" / "attribution.csv", index=False)


def _variant_root(base, spec):
    return replay.bind_variant(spec, base)


# ── 网格构造 ────────────────────────────────────────────────────


def test_grid_includes_baseline_and_dedupes_it():
    specs = l2_grid.grid_specs(caps=[0.15, 0.20, 0.25])
    names = [s.name for s in specs]
    assert names[0] == "baseline"
    assert "cap020_floor100" not in names       # 与 baseline 同定义,不重复占根
    assert len(specs) == 3


def test_each_grid_point_has_a_distinct_hash():
    specs = l2_grid.grid_specs(caps=[0.10, 0.15, 0.20, 0.25],
                               floor_scales=[0.5, 1.0, 1.5])
    hashes = [s.definition_hash for s in specs]
    assert len(set(hashes)) == len(hashes)


def test_plan_creates_independent_roots(tmp_path):
    rows = l2_grid.plan(l2_grid.grid_specs(caps=[0.15, 0.25]), tmp_path)
    roots = {r["output_root"] for r in rows}
    assert len(roots) == 3                     # baseline + 两个格点
    for row in rows[1:]:
        assert "_v_" in row["output_root"]


def test_floor_scaling_produces_integer_floors():
    specs = l2_grid.grid_specs(caps=[0.20], floor_scales=[0.5])
    scaled = next(s for s in specs if s.name != "baseline")
    assert all(isinstance(v, int) for v in scaled.floors.values())


# ── 打分:FDR + 守卫 + 不裁决 ──────────────────────────────────


def _populate(tmp_path, *, variant_winner_bonus=0):
    """baseline 与一个格点各 12 天;格点多捞 `variant_winner_bonus` 个赢家。"""
    for i in range(1, 13):
        date = f"2026-06-{i:02d}"
        _day(tmp_path, date, l2_codes=["900001"], winners=["900001", "900002"])
    spec = l2_grid.grid_specs(caps=[0.15])[1]
    vroot = _variant_root(tmp_path, spec)
    for i in range(1, 13):
        date = f"2026-06-{i:02d}"
        caught = ["900001", "900002"][: 1 + variant_winner_bonus]
        _day(vroot, date, l2_codes=caught, winners=["900001", "900002"])
    return spec


def test_score_reports_paired_deltas_against_baseline(tmp_path):
    _populate(tmp_path, variant_winner_bonus=1)
    payload = l2_grid.score(tmp_path)
    assert payload["baseline"]["n_days"] == 12
    delta = payload["deltas"][0]
    assert delta["n_paired_days"] == 12
    assert delta["delta"]["point"] > 0          # 格点多捞了一个赢家


def test_top_point_is_labelled_exploratory_not_recommended(tmp_path):
    _populate(tmp_path, variant_winner_bonus=1)
    payload = l2_grid.score(tmp_path)
    best = payload["exploratory_best"]
    assert best["label"] == "EXPLORATORY_BEST"
    assert "不是建议采用" in best["caveat"]


def test_decision_policy_points_at_walk_forward(tmp_path):
    _populate(tmp_path)
    payload = l2_grid.score(tmp_path)
    assert "walk-forward" in payload["decision_policy"]
    assert "holdout" in payload["decision_policy"]


def test_multiple_testing_is_applied(tmp_path):
    _populate(tmp_path)
    payload = l2_grid.score(tmp_path)
    assert payload["n_hypotheses"] >= 1
    for row in payload["deltas"]:
        assert "q" in row
        assert row["status"] in ("SIGNIFICANT_EXPLORATORY", "NOT_SIGNIFICANT")


def test_guard_breach_disqualifies_a_point_regardless_of_slo(tmp_path):
    """集中度破线 → GUARD_BREACH,SLO 再好也不进 deltas。

    格点全押一个行业、一条 lane,而且**捞到了全部赢家** —— winner capture 满分。
    正是「靠集中追热点把主 SLO 做高」的形态,守卫必须把它挡下来。
    """
    for i in range(1, 13):
        _day(tmp_path, f"2026-06-{i:02d}", l2_codes=["900001"], winners=["900001"])
    spec = l2_grid.grid_specs(caps=[0.15])[1]
    vroot = _variant_root(tmp_path, spec)
    for i in range(1, 13):
        _day(vroot, f"2026-06-{i:02d}", l2_codes=["900001", "900002", "900003"],
             winners=["900001"], industries=["电子"] * 3,
             lanes=["momentum"] * 3, filler=0)
    payload = l2_grid.score(tmp_path)
    breached = [p for p in payload["points"] if p.get("guard_status") == "GUARD_BREACH"]
    assert breached
    assert "行业集中度超线" in breached[0]["guards"]["breaches"]
    assert "lane 覆盖不足" in breached[0]["guards"]["breaches"]
    assert not any(d["name"] == breached[0]["name"] for d in payload["deltas"])


def test_missing_baseline_marks_points_no_baseline(tmp_path):
    spec = l2_grid.grid_specs(caps=[0.15])[1]
    vroot = _variant_root(tmp_path, spec)
    for i in range(1, 13):
        _day(vroot, f"2026-06-{i:02d}", l2_codes=["900001"], winners=["900001"])
    payload = l2_grid.score(tmp_path)
    assert payload["baseline"] is None
    assert payload["deltas"][0]["status"] == "NO_BASELINE"


def test_variant_root_without_data_is_marked_no_data(tmp_path):
    spec = l2_grid.grid_specs(caps=[0.15])[1]
    _variant_root(tmp_path, spec)
    payload = l2_grid.score(tmp_path)
    assert any(p["status"] == "NO_DATA" for p in payload["points"])


def test_capfloor20_is_not_treated_as_a_prior(tmp_path):
    _populate(tmp_path)
    payload = l2_grid.score(tmp_path)
    assert "L0 市值 floor" in payload["capfloor20_note"]
    assert "不是 L2 cap/floor 的近亲实证" in payload["capfloor20_note"]


# ── 渲染 / CLI ──────────────────────────────────────────────────


def test_render_leads_with_the_exploration_warning(tmp_path):
    _populate(tmp_path)
    md = l2_grid.render(l2_grid.score(tmp_path))
    assert "探索,非裁决" in md and "EXPLORATORY_BEST" in md


def test_cli_plan_and_score(tmp_path, capsys):
    assert l2_grid.main(["plan", "--root", str(tmp_path), "--caps", "0.15,0.25"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3
    _populate(tmp_path)
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    assert l2_grid.main(["score", "--root", str(tmp_path), "--json-out", str(out_json),
                         "--md-out", str(out_md)]) == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["metric"] == "wc_l2_all"
