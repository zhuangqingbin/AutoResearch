#!/usr/bin/env python3
"""漏斗历史回放器(research.replay)——PIT 卫兵 / 幂等断点续跑 / M1 对拍 / R1-R3 聚合。

design: docs/specs/2026-07-12-funnel-replay-l35-removal-design.md Part B。

不打网络:universe.run / retro.attribute / temperature.rollup / forward_returns 全部 monkeypatch,
只验编排与聚合契约(取数正确性由既有 universe/retro/channel_audit 测试覆盖——回放不重造它们)。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.research import replay

# ───────────────────────── PIT 卫兵(§1 权重 / §2 端点) ─────────────────────────


def test_assert_pit_source_rejects_akshare():
    """PIT §2:akshare/em 路径含 live spot 快照 → 回放历史日会读到今日盘口(灾难性前视偏差),
    必须响亮拒绝而不是静默降级。"""
    with pytest.raises(ValueError, match="PIT"):
        replay._assert_pit_source("akshare")
    replay._assert_pit_source("tushare")            # 唯一允许的 EOD 源


def test_weights_path_prior_writes_snapshot_without_regimes_block(tmp_path):
    """PIT §1:prior = 内置先验快照(零校准=零泄漏)。关键契约——快照**无 regimes 块**,
    这样 `_load_weights(path, regime=<label>)` 的 regime 分支落空、退回 flat 先验;
    regime 标签本身仍照算(标签 PIT 安全,只有权重值带未来信息)。"""
    p = replay.weights_path_for("prior", tmp_path)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    assert "regimes" not in data, "先验快照不得含 regimes 块(否则又把校准过的权重喂回去了)"
    assert data["weights"]["__global__"]["momentum"] == pytest.approx(0.10)

    from autoresearch.common.scoring import _load_weights
    # 真实装载路径:即便传了 regime,也应退回先验 flat(零泄漏)
    w = _load_weights(p, regime="risk_off")
    assert w["weights"]["__global__"]["momentum"] == pytest.approx(0.10)


def test_weights_path_current_returns_none_meaning_production_default(tmp_path):
    """current = None → universe.run 吃 pick_weights 默认路径(现 weights.json,**有泄漏**),
    仅供 M1 对拍/对照。"""
    assert replay.weights_path_for("current", tmp_path) is None


def test_weights_path_explicit_path_passthrough(tmp_path):
    """显式路径原样透传(M1 对拍传生产当日 weights_used.json → 精确复现当日权重)。"""
    assert replay.weights_path_for(str(tmp_path / "weights_used.json"), tmp_path) == \
        str(tmp_path / "weights_used.json")


def test_universe_run_weights_path_none_is_parity():
    """parity 铁律:`universe.run(weights_path=None)` 不得给 pick_weights 传 path
    (= 现行为,生产路径逐字节不变)。"""
    import inspect

    from autoresearch.scan import universe
    src = inspect.getsource(universe.run)
    assert '{"path": weights_path} if weights_path else {}' in src


# ───────────────────────── 回放循环:幂等 / 断点续跑 / 失败隔离 ─────────────────────────


def _fake_staging(sdir):
    sdir.mkdir(parents=True, exist_ok=True)
    for f in replay._STAGING:
        (sdir / f).write_text("{}" if f.endswith(".json") else "code\n000001\n", encoding="utf-8")


def test_replay_day_skips_when_complete(tmp_path, monkeypatch):
    """幂等:_STAGING 全在场 → 跳过(500 日批任务中断后重跑只补缺口,这是断点续跑的基石)。"""
    _fake_staging(tmp_path / "2026-06-01")
    called = []
    monkeypatch.setattr("autoresearch.scan.universe.run", lambda *a, **k: called.append(1) or {})
    r = replay.replay_day("2026-06-01", tmp_path)
    assert r["status"] == "skip"
    assert not called, "已完成的日不应重跑 universe"


def test_replay_day_force_reruns_even_when_complete(tmp_path, monkeypatch):
    _fake_staging(tmp_path / "2026-06-01")
    monkeypatch.setattr("autoresearch.scan.universe.run",
                        lambda *a, **k: {"l2_n": 200, "recall_n": 1000, "universe": 4000})
    monkeypatch.setattr("autoresearch.learning.retro.attribute",
                        lambda *a, **k: pd.DataFrame({"code": ["000001"], "winner": [True]}))
    r = replay.replay_day("2026-06-01", tmp_path, force=True)
    assert r["status"] == "ok" and r["l2_n"] == 200 and r["winners"] == 1


def test_replay_day_passes_production_shape_kwargs(tmp_path, monkeypatch):
    """对齐生产真身(prelude 只传 date+regime_aware):outdir 指向回放根、shadow 关、
    regime_aware 开、weights_path 注入 —— 任一漂移都会让回放悄悄偏离生产漏斗。"""
    seen = {}

    def _fake_run(date, **kw):
        seen.update(date=date, **kw)
        return {"l2_n": 1, "recall_n": 1, "universe": 1}

    monkeypatch.setattr("autoresearch.scan.universe.run", _fake_run)
    replay.replay_day("2026-06-01", tmp_path, attribute=False)
    assert seen["outdir"] == tmp_path / "2026-06-01"
    assert seen["shadow"] is False and seen["regime_aware"] is True
    assert seen["source"] == "tushare"
    assert seen["weights_path"].endswith("_weights_prior.json")


def test_immature_fwd_does_not_kill_the_day(tmp_path, monkeypatch):
    """窗口末尾几天的 fwd(D+2 未收盘)算不出来 —— 但那天的**漏斗产物是好的**,不该判死。
    标 attr_pending 留着,等成熟后 backfill(冒烟实证:回放到 07-09,其 fwd_2 要等 07-13 收盘)。"""
    monkeypatch.setattr("autoresearch.scan.universe.run",
                        lambda *a, **k: {"l2_n": 200, "recall_n": 1000, "universe": 4000})
    monkeypatch.setattr("autoresearch.learning.retro.attribute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fwd 未实现 / 无价格")))
    r = replay.replay_day("2026-07-09", tmp_path)
    assert r["status"] == "ok", "漏斗跑成了就是 ok —— 归因未成熟是时间问题,不是失败"
    assert "fwd 未实现" in r["attr_pending"]


def test_backfill_attribution_only_targets_missing_days(tmp_path, monkeypatch):
    """补归因是独立入口:幂等判据 `_STAGING` 不含 attribution,否则这些日子会被"已完成"跳过、
    归因永远补不上。"""
    for d in ("2026-06-01", "2026-06-02"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "L1_scored_full.csv").write_text("code\n000001\n", encoding="utf-8")
    (tmp_path / "2026-06-01" / "retro").mkdir()
    (tmp_path / "2026-06-01" / "retro" / "attribution.csv").write_text("code\n", encoding="utf-8")

    seen = []
    monkeypatch.setattr("autoresearch.learning.retro.attribute",
                        lambda date, **k: seen.append(date) or pd.DataFrame({"winner": [True]}))
    res = replay.backfill_attribution(tmp_path)
    assert seen == ["2026-06-02"], "已有 attribution 的日子不重算"
    assert res["done"] == ["2026-06-02"]


def test_run_isolates_single_day_failure_and_continues(tmp_path, monkeypatch):
    """单日失败(限频/权限/网络偶发)不得中断整段——记 failed 继续,断点续跑再补。"""
    monkeypatch.setattr(replay, "trade_days_iso",
                        lambda s, e: ["2026-06-01", "2026-06-02", "2026-06-03"])
    monkeypatch.setattr(replay, "replay_day", lambda d, root, **kw: (
        (_ for _ in ()).throw(RuntimeError("tushare 限频")) if d == "2026-06-02"
        else {"date": d, "status": "ok"}))
    monkeypatch.setattr("autoresearch.scan.temperature.rollup", lambda *a, **k: None)
    res = replay.run("2026-06-01", "2026-06-03", tmp_path)
    assert res["done"] == ["2026-06-01", "2026-06-03"]
    assert len(res["failed"]) == 1 and res["failed"][0]["date"] == "2026-06-02"
    assert json.loads((tmp_path / "_run_summary.json").read_text(encoding="utf-8"))["days"] == 3


def test_run_records_weights_leak_flag(tmp_path, monkeypatch):
    """`weights=current` 必须在 summary 里留下 weights_leak=True(报告要如实标注泄漏)。"""
    monkeypatch.setattr(replay, "trade_days_iso", lambda s, e: ["2026-06-01"])
    monkeypatch.setattr(replay, "replay_day", lambda d, root, **kw: {"date": d, "status": "ok"})
    monkeypatch.setattr("autoresearch.scan.temperature.rollup", lambda *a, **k: None)
    assert replay.run("2026-06-01", "2026-06-01", tmp_path, weights="current")["weights_leak"] is True
    assert replay.run("2026-06-01", "2026-06-01", tmp_path, weights="prior")["weights_leak"] is False


# ───────────────────────── M1 对拍 ─────────────────────────


def _prod_day(root, date, codes=("000001", "000002")):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    for f in replay._DIFF_FILES:
        pd.DataFrame({"code": list(codes)}).to_csv(d / f, index=False)
    (d / "meta.json").write_text(json.dumps({"universe_raw": 5400, "universe": 4000,
                                             "after_gate_a": 3800, "recall_n": 1000,
                                             "l2_n": 200, "l2_engine": "stratified",
                                             "regime": "range"}), encoding="utf-8")
    return d


def test_diff_day_identical_is_ok(tmp_path):
    prod, rep = tmp_path / "prod", tmp_path / "rep"
    _prod_day(prod, "2026-07-09")
    _prod_day(rep, "2026-07-09")
    r = replay.diff_day("2026-07-09", prod, rep)
    assert r["ok"] is True
    assert all(f["identical"] for f in r["files"].values())
    assert r["meta"] == {"identical": True}


def test_diff_day_reports_code_set_overlap_when_differing(tmp_path):
    """不一致时给的是可诊断的结构化读数(jaccard/only_prod/only_replay),不是一句 'diff'
    —— 三类合法差异(配置漂移/权重漂移/数据漂移)靠这些数字区分。"""
    prod, rep = tmp_path / "prod", tmp_path / "rep"
    _prod_day(prod, "2026-07-09", codes=("000001", "000002"))
    _prod_day(rep, "2026-07-09", codes=("000002", "000003"))
    r = replay.diff_day("2026-07-09", prod, rep)
    assert r["ok"] is False
    f = r["files"]["L1_scored_full.csv"]
    assert f["common"] == 1 and f["only_prod"] == 1 and f["only_replay"] == 1
    assert f["jaccard"] == pytest.approx(1 / 3, abs=1e-3)


def test_diff_day_missing_replay_dir(tmp_path):
    prod = tmp_path / "prod"
    _prod_day(prod, "2026-07-09")
    r = replay.diff_day("2026-07-09", prod, tmp_path / "rep")
    assert r["ok"] is False and "缺目录" in r["reason"]


def test_diff_day_meta_regime_mismatch_flagged(tmp_path):
    prod, rep = tmp_path / "prod", tmp_path / "rep"
    _prod_day(prod, "2026-07-09")
    d = _prod_day(rep, "2026-07-09")
    m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    m["regime"] = "risk_off"
    (d / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    r = replay.diff_day("2026-07-09", prod, rep)
    assert r["ok"] is False
    assert r["meta"]["regime"] == {"prod": "range", "replay": "risk_off"}


def test_asof_funnel_uses_registry_not_产物_reconstruction(tmp_path):
    """M1 消差异①(配置漂移)的**回归测试**——第一版从 L1_channels.csv 反推"当日跑了哪几路",
    被 M1 实跑当场证伪:07-09 产物只有 9 路,当日真实传的是注册表全 11 路,healthy/
    reversal_confirm 那天**召回 0 只**(零召回的路在产物里不可见)。少 2 路 → 配额分配变了 →
    召回池差 5%。**产物能证明"跑过什么",不能证明"没跑过什么"。**"""
    from autoresearch.scan.recall import registered_channels

    # 产物只见 2 路,但 as-of 必须给全注册表(否则重蹈那 5% 的覆辙)
    pd.DataFrame({"channel": ["value", "momentum"], "code": ["1", "2"]}).to_csv(
        tmp_path / "L1_channels.csv", index=False)
    f = replay.asof_funnel(tmp_path)
    assert f["recall_channels"] == list(registered_channels())
    assert len(f["recall_channels"]) > 2, "零召回的路必须仍在场"
    assert f["channel_quotas"] == {} and f["channel_floors"] == {}


def test_m1_disables_pinned_injection(tmp_path, monkeypatch):
    """M1 消差异③(保送漂移):pinned.jsonc 是**当下**的持仓清单(会强注 L1 → recall_n +N),
    历史生产日没有它 —— M1 实跑正是被这个 +1 只的 recall_n 差异逮到的。"""
    seen = {}
    monkeypatch.setattr(replay, "replay_day", lambda d, root, **kw: seen.update(kw))
    monkeypatch.setattr(replay, "diff_day", lambda *a, **k: {"ok": True, "files": {}})
    monkeypatch.setattr(replay, "asof_weights", lambda p: "current")
    replay.m1_check("2026-07-09", tmp_path / "prod", tmp_path / "rep")
    assert str(seen["pinned_path"]).endswith("_no_pinned.json")
    assert not seen["pinned_path"].exists(), "指向不存在的文件 → load_pinned 返回 kept=[] → 无保送"


def test_asof_weights_prefers_day_snapshot_else_current(tmp_path):
    """M1 消差异②(权重漂移):weights.json 被 retro 持续重标定 → 用当日 weights_used.json 快照。"""
    assert replay.asof_weights(tmp_path) == "current"
    (tmp_path / "weights_used.json").write_text("{}", encoding="utf-8")
    assert replay.asof_weights(tmp_path) == str(tmp_path / "weights_used.json")


def test_replay_day_funnel_kwargs_reach_universe_run(tmp_path, monkeypatch):
    """funnel 必须真的透传到 universe.run(FN-1 教训:接了参数 ≠ 生效)。"""
    seen = {}
    monkeypatch.setattr("autoresearch.scan.universe.run",
                        lambda date, **kw: seen.update(kw) or {"l2_n": 1, "recall_n": 1, "universe": 1})
    replay.replay_day("2026-06-01", tmp_path, attribute=False,
                      funnel={"recall_channels": ["value"], "channel_quotas": {}, "channel_floors": {}})
    assert seen["recall_channels"] == ["value"]
    assert seen["channel_quotas"] == {} and seen["channel_floors"] == {}


# ───────────────────────── 聚合:R1 / R2 / R3 ─────────────────────────


def test_phase_map_presence_gated(tmp_path):
    assert replay.phase_map(tmp_path / "nope.csv") == {}
    p = tmp_path / "t.csv"
    pd.DataFrame({"date": ["2026-06-01", "2026-06-02"], "phase": ["冰点", "修复"]}).to_csv(p, index=False)
    assert replay.phase_map(p) == {"2026-06-01": "冰点", "2026-06-02": "修复"}


def _attr_day(root, date, winners_by_bucket: dict[str, int], buyable=True):
    d = root / date / "retro"
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    i = 0
    for bucket, n in winners_by_bucket.items():
        for _ in range(n):
            rows.append({"code": f"{i:06d}", "winner": True, "bucket": bucket, "buyable": buyable})
            i += 1
    rows.append({"code": f"{i:06d}", "winner": False, "bucket": "", "buyable": True})
    pd.DataFrame(rows).to_csv(d / "attribution.csv", index=False)


def test_winner_autopsy_groups_by_phase_and_computes_missed_l1_pct(tmp_path):
    """R3 核心读数:missed_l1 占比(召回线瓶颈的大样本复核),按相位分组。"""
    _attr_day(tmp_path, "2026-06-01", {"missed_l1": 6, "missed_l0": 2, "recalled_cut": 2})
    _attr_day(tmp_path, "2026-06-02", {"missed_l1": 4, "recalled_cut": 1})
    ph = {"2026-06-01": "冰点", "2026-06-02": "冰点"}
    df = replay.winner_autopsy(tmp_path, ph)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["phase"] == "冰点" and row["n_days"] == 2
    assert row["n_winners"] == 15 and row["missed_l1"] == 10
    assert row["missed_l1_pct"] == pytest.approx(66.7, abs=0.1)
    assert row["caught"] == 0, "回放无买单 → 恒无 caught(固有边界,不是 bug)"


def test_winner_autopsy_excludes_unbuyable_winners(tmp_path):
    """PIT §5:D+1 一字板/停牌不可买 → 不得计入赢家(否则捕获率是虚假的)。"""
    _attr_day(tmp_path, "2026-06-01", {"missed_l1": 5}, buyable=False)
    assert replay.winner_autopsy(tmp_path, {"2026-06-01": "冰点"}).empty


def test_winner_autopsy_empty_root(tmp_path):
    assert replay.winner_autopsy(tmp_path, {}).empty


def test_channel_by_phase_splits_ledger_by_phase(tmp_path, monkeypatch):
    """R2:同一批日子按相位切成多份累计账本 + `__all__` 全窗账本(可与前向 13 日账本对照)。
    单日口径复用生产 channel_audit.day_channel_stats —— 回放不重造通道口径。"""
    for date in ("2026-06-01", "2026-06-02"):
        d = tmp_path / date
        (d / "retro").mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"channel": ["momentum", "value"], "code": ["000001", "000002"],
                      "channel_rank": [1, 1], "channel_score": [1.0, 1.0]}).to_csv(
            d / "L1_channels.csv", index=False)
        pd.DataFrame({"code": ["000001", "000002", "000003"],
                      "fwd_2_oc": [0.05, -0.01, 0.0],
                      "buyable": [True, True, True]}).to_csv(d / "retro" / "attribution.csv", index=False)
    out = replay.channel_by_phase(tmp_path, phases={"2026-06-01": "冰点", "2026-06-02": "发酵"})
    assert set(out) == {"__all__", "冰点", "发酵"}
    assert out["__all__"]["n_days"].max() == 2      # 全窗两天
    assert out["冰点"]["n_days"].max() == 1          # 单相位各一天
    assert "unique_excess_t2" in out["__all__"].columns


def test_channel_by_phase_empty_root_returns_empty(tmp_path):
    assert replay.channel_by_phase(tmp_path, phases={}) == {}


def test_phase_returns_marks_thin_samples(tmp_path, monkeypatch):
    """R1:n<10 的相位标 thin(与 cross_calib/buy_ledger 同款薄样本禁注惯例)。"""
    monkeypatch.setattr(
        "autoresearch.scan.temperature_calib.forward_returns",
        lambda dates: pd.DataFrame({"date": dates,
                                    "fwd_1": [0.01] * len(dates),
                                    "fwd_2": [0.02] * len(dates)}))
    ph = {f"2026-06-{i:02d}": "冰点" for i in range(1, 4)}
    df = replay.phase_returns(tmp_path, ph)
    assert df.iloc[0]["n"] == 3 and bool(df.iloc[0]["thin"]) is True
    assert df.iloc[0]["mean_fwd_2"] == pytest.approx(0.02)


def test_report_writes_md_with_leak_warning(tmp_path, monkeypatch):
    """报告必须如实标注权重泄漏(weights=current 时)——研究仪器的诚实底线。"""
    monkeypatch.setattr(replay, "phase_map", lambda *a, **k: {})
    monkeypatch.setattr(replay, "phase_returns", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(replay, "channel_by_phase", lambda *a, **k: {})
    monkeypatch.setattr(replay, "winner_autopsy", lambda *a, **k: pd.DataFrame())
    (tmp_path / "2026-06-01").mkdir(parents=True)
    p = replay.report("2026-06-01", "2026-06-30", tmp_path, tmp_path / "out", weights="current")
    text = p.read_text(encoding="utf-8")
    assert "⚠ 有泄漏" in text
    assert "L3/L4(LLM 判断层)不可回放" in text

    p2 = replay.report("2026-06-01", "2026-06-30", tmp_path, tmp_path / "out2", weights="prior")
    assert "零校准=零泄漏" in p2.read_text(encoding="utf-8")


def test_frame_integrity_catches_silently_degraded_factor_group(tmp_path):
    """因子帧完整性门(2026-07-12 事故的守卫):volprice 组的列整列消失 → 必须当场检出。

    真实事故:lake 的 daily parquet 被窄表毒化 → `_harvest_vol_series` 静默返回空帧 →
    cmf_20/obv_mom_20 整列不落盘 → composite 对缺列的组自动 NaN 重归一 → 漏斗照常跑完,
    但全市场打分失真 98.8%、L2 名单 jaccard 掉到 0.36。唯一信号是一行淹没的 warn。"""
    d = tmp_path / "2026-07-09"
    d.mkdir()
    pd.DataFrame({"code": ["000001"], "composite": [1.0], "pct_60d": [1.0],
                  "main_net_ratio": [0.1]}).to_csv(d / "L1_scored_full.csv", index=False)
    assert replay.frame_integrity(d) == ["cmf_20", "obv_mom_20"]

    pd.DataFrame({"code": ["000001"], "composite": [1.0], "cmf_20": [0.1], "obv_mom_20": [0.2],
                  "pct_60d": [1.0], "main_net_ratio": [0.1]}).to_csv(
        d / "L1_scored_full.csv", index=False)
    assert replay.frame_integrity(d) == []


def test_frame_integrity_missing_frame(tmp_path):
    assert replay.frame_integrity(tmp_path) == ["L1_scored_full.csv"]


def test_replay_day_flags_degraded_frame(tmp_path, monkeypatch):
    """回放单日若因子帧退化 → summary 带 degraded 标记(不能让被污染的日子悄悄进统计)。"""
    def _fake_run(date, **kw):
        sd = kw["outdir"]
        sd.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"code": ["000001"], "composite": [1.0], "pct_60d": [1.0],
                      "main_net_ratio": [0.1]}).to_csv(sd / "L1_scored_full.csv", index=False)
        return {"l2_n": 200, "recall_n": 1000, "universe": 4000}

    monkeypatch.setattr("autoresearch.scan.universe.run", _fake_run)
    r = replay.replay_day("2026-07-09", tmp_path, attribute=False)
    assert r["degraded"] == ["cmf_20", "obv_mom_20"]


def test_survivorship_probe_reports_l0_gap(tmp_path):
    """PIT §4:幸存者缺口如实报告(stock_basic('L') 只含当前存续股 → 退市票可能掉队)。"""
    d = tmp_path / "2026-06-01"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"universe_raw": 5000, "universe": 4000,
                                             "after_gate_a": 3800}), encoding="utf-8")
    probe = replay.survivorship_probe(d)
    assert probe["l0_kept_pct"] == pytest.approx(80.0)
    assert replay.survivorship_probe(tmp_path / "nope") is None
