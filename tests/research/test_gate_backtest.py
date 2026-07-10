"""gate_backtest 历史重放单测 —— 合成两日 judged+attribution + 桩 gate_fn,零网络。

覆盖 Plan A2 Task 2(docs/plans/2026-07-11-l35-gate-backtest-plan.md):
  - replay_day:单日 picked 集 mean_fwd2/hit/n 算对;落选赢家清单(cut 且 fwd_2_oc>win_thr)命中/放过均对
  - GateResult 鸭子类型:dict({picked,cut}) 与带 .picked/.cut 属性的对象(SimpleNamespace)两种落地都兼容
    (registry 隔离:l35_gate.py 由另一 agent 并行开发,本模块/本测试全程注入桩 gate_fn,不依赖其存在)
  - resolve_regime:meta.json 优先,缺失时回退 classify_regime(judged) 现算
  - run_backtest:跨两日池化(overall)+ 分 regime 小节 + cut_winners 汇总排序
  - discover_dates:只认"L3_judged_full.csv 与 retro/attribution.csv 同时存在"的日子
  - _parse_params_grid:单 object / array(网格)/ 缺省 三态
  - CLI main():registry 未就位(_build_gate=None)→ 优雅提示 + rc=1;happy path(monkeypatch 注入桩 registry)
    → rc=0 且落盘报告含 gate 名
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.research import gate_backtest as gb

# ─────────────────────────── 合成桩数据 ───────────────────────────

# Day 1: 2099-01-01,regime=trend(meta.json 显式),conviction 降序 000001>2>3>4>5
# (6 位数字 code——与真实 A 股代码同形,避免 zfill(6) 补零把可读别名"A1"之类改写成"0000A1")
_DAY1_JUDGED = pd.DataFrame({
    "code": ["000001", "000002", "000003", "000004", "000005"],
    "name": ["Alpha", "Bravo", "Charlie", "Delta", "Echo"],
    "sector": ["半导体"] * 5,
    "conviction": [90, 80, 70, 60, 50],
    "fragility": [10, 20, 30, 40, 50],
    "lane": ["trend"] * 5,
    "pct_60d": [30, 25, 20, 15, 10],
})
# fwd_2_oc: picked(1,2)=0.05/0.02;cut 里 3=0.10(错杀,>win_thr)、4=-0.01、5=0.00(不算错杀)
_DAY1_ATTR = pd.DataFrame({
    "code": ["000001", "000002", "000003", "000004", "000005"],
    "fwd_2_oc": [0.05, 0.02, 0.10, -0.01, 0.00],
})

# Day 2: 2099-01-02,regime=risk_off(meta.json 显式),conviction 降序 000006>7>8>9>10
_DAY2_JUDGED = pd.DataFrame({
    "code": ["000006", "000007", "000008", "000009", "000010"],
    "name": ["Foxtrot", "Golf", "Hotel", "India", "Juliet"],
    "sector": ["银行"] * 5,
    "conviction": [95, 85, 75, 65, 55],
    "fragility": [5, 15, 25, 35, 45],
    "lane": ["value"] * 5,
    "pct_60d": [-30, -25, -20, -15, -10],
})
# fwd_2_oc: picked(6,7)=0.01/-0.02;cut 里 8=0.08(错杀)、9=0.00、10=0.04(错杀,踩线超过 0.03)
_DAY2_ATTR = pd.DataFrame({
    "code": ["000006", "000007", "000008", "000009", "000010"],
    "fwd_2_oc": [0.01, -0.02, 0.08, 0.00, 0.04],
})


def _stub_gate_dict(judged: pd.DataFrame, *, regime: str, exempt: set, params: dict) -> dict:
    """桩 gate_fn(Task1 签名一致):按 conviction 降序取 top params['k'](默认2),GateResult=dict 落地。"""
    k = int(params.get("k", 2))
    ranked = judged.sort_values("conviction", ascending=False).reset_index(drop=True)
    picked = ranked.head(k)["code"].tolist()
    cut = [{"code": row["code"], "rank": i + 1, "conviction": row["conviction"],
            "lane": row.get("lane"), "reason": "cut_by_stub"}
           for i, row in ranked.iloc[k:].iterrows()]
    return {"picked": picked, "cut": cut}


def _stub_gate_namespace(judged, *, regime, exempt, params):
    """同一桩逻辑,GateResult=带 .picked/.cut 属性对象落地(验证鸭子类型解包不挑落地形态)。"""
    d = _stub_gate_dict(judged, regime=regime, exempt=exempt, params=params)
    return types.SimpleNamespace(picked=d["picked"], cut=d["cut"])


def _write_day(root: Path, date: str, judged: pd.DataFrame, attr: pd.DataFrame,
              regime: str | None = None) -> None:
    sdir = root / date
    (sdir / "retro").mkdir(parents=True, exist_ok=True)
    judged.to_csv(sdir / "L3_judged_full.csv", index=False)
    attr.to_csv(sdir / "retro" / "attribution.csv", index=False)
    if regime is not None:
        (sdir / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


# ─────────────────────────── replay_day ───────────────────────────


def test_replay_day_picked_mean_and_hit():
    out = gb.replay_day(_DAY1_JUDGED, _DAY1_ATTR, _stub_gate_dict, regime="trend", params={"k": 2})
    pm = out["picked_metrics"]
    assert pm["n"] == 2
    assert abs(pm["mean_fwd2"] - 0.035) < 1e-9          # (0.05+0.02)/2
    assert pm["hit"] == 1.0                              # 两只都 >0
    assert out["n_judged"] == 5
    assert out["n_picked"] == 2
    assert out["n_cut"] == 3


def test_replay_day_cut_winners_flagged_correctly():
    out = gb.replay_day(_DAY1_JUDGED, _DAY1_ATTR, _stub_gate_dict, regime="trend", params={"k": 2})
    winners = out["cut_winners"]
    codes = {w["code"] for w in winners}
    assert codes == {"000003"}                            # 只有 000003(0.10)超过默认 win_thr=0.03
    assert "000004" not in codes and "000005" not in codes  # -0.01/0.00 均不算错杀
    assert winners[0]["fwd_2_oc"] == 0.10
    assert winners[0]["reason"] == "cut_by_stub"          # 原 cut 行字段透传


def test_replay_day_win_thr_is_configurable():
    out = gb.replay_day(_DAY2_JUDGED, _DAY2_ATTR, _stub_gate_dict, regime="risk_off",
                        params={"k": 2}, win_thr=0.05)
    codes = {w["code"] for w in out["cut_winners"]}
    assert codes == {"000008"}                            # win_thr=0.05 → 000010(0.04)不再算错杀


def test_replay_day_dedupes_duplicate_attribution_codes():
    """真实数据实测坑(2026-06-24 retro/attribution.csv 里 002657 重复两行,rank 微差、其余全同)——
    `attribution.set_index('code')` 前若不去重,reindex 会抛 `cannot reindex on an axis with
    duplicate labels`。用真实坑的形状回归:同一 code 出现两次,断言不崩且不因重复行虚增样本数。
    """
    dup_attr = pd.concat([_DAY1_ATTR, _DAY1_ATTR.iloc[[0]]], ignore_index=True)   # 000001 重复一行
    out = gb.replay_day(_DAY1_JUDGED, dup_attr, _stub_gate_dict, regime="trend", params={"k": 2})
    assert out["picked_metrics"]["n"] == 2                # 仍是 2(000001/000002),非 3


def test_replay_day_accepts_dict_and_namespace_gate_result():
    out_dict = gb.replay_day(_DAY1_JUDGED, _DAY1_ATTR, _stub_gate_dict, regime="trend", params={"k": 2})
    out_ns = gb.replay_day(_DAY1_JUDGED, _DAY1_ATTR, _stub_gate_namespace, regime="trend", params={"k": 2})
    assert out_dict["picked_metrics"] == out_ns["picked_metrics"]
    assert {w["code"] for w in out_dict["cut_winners"]} == {w["code"] for w in out_ns["cut_winners"]}


# ─────────────────────────── resolve_regime ───────────────────────────


def test_resolve_regime_prefers_meta_json(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    # judged 的 pct_60d 全正(会被 classify_regime 判成非 risk_off)—— meta.json 显式值必须优先,不被现算覆盖
    assert gb.resolve_regime(tmp_path / "2099-01-01", _DAY1_JUDGED) == "trend"


def test_resolve_regime_falls_back_to_classify_regime_when_no_meta(tmp_path):
    from autoresearch.common.regime import classify_regime

    sdir = tmp_path / "2099-01-02"
    sdir.mkdir(parents=True)                              # 无 meta.json
    # 全部 pct_60d 深跌 → breadth=0、med_mom<0 → classify_regime 应判 risk_off
    deep_down = _DAY2_JUDGED.assign(pct_60d=[-60, -55, -50, -45, -40])
    expected = classify_regime(deep_down).label
    assert expected == "risk_off"                         # 前提:本用例数据确实能触发非中性判定
    assert gb.resolve_regime(sdir, deep_down) == expected


def test_resolve_regime_no_dir_no_meta_uses_classify_regime():
    assert gb.resolve_regime(None, _DAY1_JUDGED) in {"trend", "range", "risk_off"}


# ─────────────────────────── discover_dates ───────────────────────────


def test_discover_dates_requires_both_files(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    (tmp_path / "2099-01-03").mkdir()                     # 只有目录,啥都没有
    (tmp_path / "2099-01-04" / "retro").mkdir(parents=True)
    _DAY2_JUDGED.to_csv(tmp_path / "2099-01-04" / "L3_judged_full.csv", index=False)
    # 2099-01-04 缺 retro/attribution.csv → 不应入选
    assert gb.discover_dates(tmp_path) == ["2099-01-01"]


def test_discover_dates_empty_root_returns_empty(tmp_path):
    assert gb.discover_dates(tmp_path / "nonexistent") == []


# ─────────────────────────── run_backtest(跨日池化 + 分 regime) ───────────────────────────


def test_run_backtest_pools_across_two_days(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    _write_day(tmp_path, "2099-01-02", _DAY2_JUDGED, _DAY2_ATTR, regime="risk_off")

    res = gb.run_backtest(["2099-01-01", "2099-01-02"], gate_fn=_stub_gate_dict, gate_name="stub",
                          params={"k": 2}, scan_root=tmp_path)

    assert res["n_days"] == 2
    assert res["skipped_dates"] == []
    ov = res["overall"]
    assert ov["n"] == 4                                   # 2 picked/day × 2 days
    # 池化均值:(0.05+0.02+0.01-0.02)/4 = 0.015
    assert abs(ov["mean_fwd2"] - 0.015) < 1e-9
    assert ov["hit"] == pytest.approx(0.75)                # 3/4 为正(A1,B1,F1;G1 为负)


def test_run_backtest_by_regime_breakdown(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    _write_day(tmp_path, "2099-01-02", _DAY2_JUDGED, _DAY2_ATTR, regime="risk_off")

    res = gb.run_backtest(["2099-01-01", "2099-01-02"], gate_fn=_stub_gate_dict, gate_name="stub",
                          params={"k": 2}, scan_root=tmp_path)

    by_regime = res["by_regime"]
    assert set(by_regime) == {"trend", "risk_off"}
    assert by_regime["trend"]["n"] == 2
    assert abs(by_regime["trend"]["mean_fwd2"] - 0.035) < 1e-9
    assert by_regime["risk_off"]["n"] == 2
    assert abs(by_regime["risk_off"]["mean_fwd2"] - (-0.005)) < 1e-9


def test_run_backtest_cut_winners_sorted_desc_with_date(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    _write_day(tmp_path, "2099-01-02", _DAY2_JUDGED, _DAY2_ATTR, regime="risk_off")

    res = gb.run_backtest(["2099-01-01", "2099-01-02"], gate_fn=_stub_gate_dict, gate_name="stub",
                          params={"k": 2}, scan_root=tmp_path)

    winners = res["cut_winners"]
    assert [w["code"] for w in winners] == ["000003", "000008", "000010"]     # 0.10 > 0.08 > 0.04
    assert winners[0]["date"] == "2099-01-01"
    assert winners[1]["date"] == "2099-01-02" and winners[2]["date"] == "2099-01-02"


def test_run_backtest_skips_missing_dates(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    res = gb.run_backtest(["2099-01-01", "2099-09-09"], gate_fn=_stub_gate_dict, gate_name="stub",
                          params={"k": 2}, scan_root=tmp_path)
    assert res["n_days"] == 1
    assert res["skipped_dates"] == ["2099-09-09"]


# ─────────────────────────── _parse_params_grid ───────────────────────────


def test_parse_params_grid_default_single_empty():
    assert gb._parse_params_grid(None) == [{}]


def test_parse_params_grid_single_object():
    assert gb._parse_params_grid('{"floor": 60}') == [{"floor": 60}]


def test_parse_params_grid_array_is_grid():
    out = gb._parse_params_grid('[{"floor": 60}, {"floor": 70}]')
    assert out == [{"floor": 60}, {"floor": 70}]


# ─────────────────────────── render_report / write_report ───────────────────────────


def test_render_report_contains_key_sections(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    res = gb.run_backtest(["2099-01-01"], gate_fn=_stub_gate_dict, gate_name="stub_gate",
                          params={"k": 2}, scan_root=tmp_path)
    md = gb.render_report([res], out_date="2099-01-09")
    assert "stub_gate" in md
    assert "落选赢家" in md
    assert "trend" in md
    assert "000003" in md                                 # 错杀审计名单里的票号出现在报告里


def test_render_report_no_results_degrades_gracefully():
    md = gb.render_report([], out_date="2099-01-09")
    assert "无可回测" in md


def test_write_report_writes_expected_path(tmp_path):
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    res = gb.run_backtest(["2099-01-01"], gate_fn=_stub_gate_dict, gate_name="stub_gate",
                          params={"k": 2}, scan_root=tmp_path)
    out_root = tmp_path / "reports"
    outp = gb.write_report([res], out_date="2099-01-09", out_root=out_root)
    assert outp == out_root / "gate_backtest_2099-01-09.md"
    assert outp.is_file()
    assert "stub_gate" in outp.read_text(encoding="utf-8")


# ─────────────────────────── CLI main() ───────────────────────────


def test_main_missing_registry_graceful(monkeypatch, tmp_path, capsys):
    """registry 未就位(_build_gate=None,并行 Task1 未落地时的真实状态)→ rc=1 + 明确提示,不崩。"""
    monkeypatch.setattr(gb, "_build_gate", None)
    monkeypatch.setattr(gb, "registered_gates", lambda: [])
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")

    rc = gb.main(["--scan-root", str(tmp_path), "--out-root", str(tmp_path / "reports")])

    assert rc == 1
    err = capsys.readouterr().err
    assert "registry" in err or "l35_gate" in err


def test_main_happy_path_with_injected_registry(monkeypatch, tmp_path, capsys):
    """monkeypatch 注入桩 registry(不依赖 l35_gate.py 是否已落地)→ rc=0 + 报告落盘。"""
    monkeypatch.setattr(gb, "_build_gate", lambda name: _stub_gate_dict)
    monkeypatch.setattr(gb, "registered_gates", lambda: ["stub_gate"])
    _write_day(tmp_path, "2099-01-01", _DAY1_JUDGED, _DAY1_ATTR, regime="trend")
    _write_day(tmp_path, "2099-01-02", _DAY2_JUDGED, _DAY2_ATTR, regime="risk_off")
    out_root = tmp_path / "reports"

    rc = gb.main(["--scan-root", str(tmp_path), "--out-root", str(out_root),
                  "--date", "2099-01-09", "--params-json", '{"k": 2}'])

    assert rc == 0
    outp = out_root / "gate_backtest_2099-01-09.md"
    assert outp.is_file()
    assert "stub_gate" in outp.read_text(encoding="utf-8")


def test_main_no_dates_found_returns_error(tmp_path, capsys):
    rc = gb.main(["--scan-root", str(tmp_path / "empty")])
    assert rc == 1
    assert capsys.readouterr().err
