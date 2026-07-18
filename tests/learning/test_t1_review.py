"""T+1 判断层复盘快环(确定性层):verdict 纯函数 / 记分卡(保送剔除·零填·基准)/
账本幂等 / pending 对 / finalize 门。合成,无网络(prices/cal 全注入)。

用户裁定 2026-07-17:快环 = T 报告真选票 vs T+1 收盘;保送不算;只做相邻交易日间隔。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.learning import t1_review as t1

_CAL = ["20260716", "20260717", "20260720"]   # 周五 07-17 → 下一交易日隔周末 07-20


def _card(rating: str) -> str:
    return f"〔卡契约 v3·超短 1~2 日〕\n# 决策卡\n**Rating**: {rating}\n**一行多空**:多:x ｜ 空:y\n"


def _mk_scan(root, t="2026-07-16"):
    d = root / t
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600001", "name": "甲", "conviction": 80, "lane": "trend"},
        {"code": "000062", "name": "乙", "conviction": 60, "lane": "value"},     # 前导零票
        {"code": "300100", "name": "丙", "conviction": 55, "lane": "reversal"},
        {"code": "600519", "name": "保", "conviction": 40, "lane": "pinned"},    # 保送:不算
    ]).to_csv(d / "finalists.csv", index=False)
    for code, rat in (("600001", "Overweight"), ("000062", "Hold"),
                      ("300100", "Underweight"), ("600519", "Hold")):
        (d / "details" / f"{code}.md").write_text(_card(rat), encoding="utf-8")
    return d


def _prices():
    # 全市场 6 只(含 2 只非 finalist,让基准 ≠ 真选均值);市场 cc1 均值 = 1%
    rows = [
        ("600001", 10.0, 10.2, 10.60, 0.060, 10.65),   # OW,cc1=+6% → 超额+5% → 准
        ("000062", 20.0, 20.0, 20.10, 0.005, 20.30),   # Hold,cc1=+0.5%
        ("300100", 30.0, 29.5, 28.50, -0.050, 29.6),   # UW,cc1=−5% → 超额−6% → 准
        ("600519", 40.0, 40.0, 40.4, 0.010, 40.5),     # pinned(应被剔除)
        ("000001", 5.0, 5.0, 5.25, 0.050, 5.3),        # 市场票
        ("000002", 8.0, 8.0, 7.96, -0.005, 8.1),       # 市场票
    ]
    df = pd.DataFrame(rows, columns=["code", "close_t", "open_t1", "close_t1", "cc1", "high_t1"])
    df["oc1"] = df["close_t1"] / df["open_t1"] - 1.0
    df["hi_oc"] = df["high_t1"] / df["open_t1"] - 1.0
    return df.drop(columns=["high_t1"])


def test_verdict_pure():
    assert t1.verdict("Overweight", 0.02) == "准"
    assert t1.verdict("Overweight", -0.02) == "不准"
    assert t1.verdict("Overweight", 0.005) == "中性"
    assert t1.verdict("Buy", 0.015) == "准"                       # 阈值含边界
    assert t1.verdict("Underweight", -0.02) == "准"
    assert t1.verdict("Sell", 0.02) == "不准"
    assert t1.verdict("Hold", 0.10) == "—"                        # Hold 无方向主张
    assert t1.verdict("Overweight", None) == "缺价"
    assert t1.verdict("Overweight", float("nan")) == "缺价"


def test_next_trade_day_injected_cal():
    assert t1.next_trade_day("2026-07-16", _CAL) == "2026-07-17"
    assert t1.next_trade_day("2026-07-17", _CAL) == "2026-07-20"  # 跨周末顺延
    assert t1.next_trade_day("2026-07-18", _CAL) is None          # 非交易日
    assert t1.next_trade_day("2026-07-20", _CAL) is None          # 日历尽头,T+1 未知


def test_build_scorecard_excludes_pinned_and_keeps_zfill(tmp_path):
    _mk_scan(tmp_path)
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    sc = res["scorecard"]
    assert res["t1"] == "2026-07-17"
    assert res["excluded"] == {"pinned": 1}
    assert set(sc["code"]) == {"600001", "000062", "300100"}       # 保送不在;前导零保住
    assert abs(res["market_cc"] - 0.0116667) < 1e-4                # 基准 = 全 6 只均值
    row = sc.set_index("code")
    assert row.loc["600001", "verdict"] == "准"                    # OW 超额 +4.8%
    assert row.loc["300100", "verdict"] == "准"                    # UW 超额 −6.2%
    assert row.loc["000062", "verdict"] == "—"
    assert bool(row.loc["300100", "surprise"])                     # |超额|≥3% 惊奇
    md = t1.render_scorecard_md(res)
    assert "剔除不计入:pinned×1" in md and "000062" in md
    assert "准 2 / 不准 0" in md


def test_ledger_idempotent_and_diagnoses_merge(tmp_path):
    _mk_scan(tmp_path)
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    lp = tmp_path / "ledger.jsonl"
    assert t1.append_ledger(res, path=lp) == 3
    assert t1.append_ledger(res, path=lp) == 3                     # 幂等:同日整替不翻倍
    rows = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and all(not r["diagnosed"] for r in rows)
    t1.append_ledger(res, diagnoses={"600001": {"mechanism": "卡内论点兑现", "why": "w"}}, path=lp)
    rows = {r["code"]: r for r in map(json.loads, lp.read_text(encoding="utf-8").splitlines())}
    assert rows["600001"]["diagnosed"] and rows["600001"]["mechanism"] == "卡内论点兑现"
    assert not rows["000062"]["diagnosed"]
    tail = t1.ledger_tail_summary(path=lp)
    assert tail["n"] == 3 and tail["direction"]["准"] == 2
    assert tail["mechanisms"] == {"卡内论点兑现": 1}


def test_pending_pairs_filters(tmp_path):
    _mk_scan(tmp_path, "2026-07-16")                               # 待复盘
    _mk_scan(tmp_path, "2026-07-15")                               # 已 done → 排除
    (tmp_path / "2026-07-15" / "t1_review").mkdir()
    (tmp_path / "2026-07-15" / "t1_review" / "done.json").write_text("{}", encoding="utf-8")
    _mk_scan(tmp_path, "2026-07-09")                               # 早于 _EPOCH → 排除
    _mk_scan(tmp_path, "2026-07-17")                               # T+1=07-20 > today → 排除
    cal = ["20260709", "20260710", "20260715", "20260716", "20260717", "20260720"]
    pairs = t1.pending_pairs(today="2026-07-17", scan_root=tmp_path, cal=cal)
    assert pairs == [{"t": "2026-07-16", "t1": "2026-07-17"}]


def test_finalize_requires_report_and_marks_done(tmp_path):
    _mk_scan(tmp_path)
    lp = tmp_path / "ledger.jsonl"
    t1.build_and_stage("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    with pytest.raises(SystemExit):                                # 综合稿没写 → 拒绝收尾
        t1.finalize("2026-07-16", scan_root=tmp_path, ledger_path=lp)
    rd = tmp_path / "2026-07-16" / "t1_review"
    (rd / "diagnoses.json").write_text(json.dumps(
        [{"code": "600001", "mechanism": "卡内论点兑现", "why": "w"}]), encoding="utf-8")
    (rd / "report.md").write_text("# 复盘\n", encoding="utf-8")
    s = t1.finalize("2026-07-16", scan_root=tmp_path, ledger_path=lp)
    assert s == {"n": 3, "diagnosed": 1, "right": 2, "wrong": 0, "promoted": []}
    done = json.loads((rd / "done.json").read_text(encoding="utf-8"))
    assert done["mode"] == "full"
    rows = {r["code"]: r for r in map(json.loads, lp.read_text(encoding="utf-8").splitlines())}
    assert rows["000062"]["code"] == "000062"                      # csv 往返前导零不丢


def test_backfill_deterministic_mode(tmp_path):
    _mk_scan(tmp_path)
    lp = tmp_path / "ledger.jsonl"
    s = t1.backfill_day("2026-07-16", scan_root=tmp_path, ledger_path=lp,
                        prices=_prices(), cal=_CAL)
    assert s["n"] == 3 and s["right"] == 2
    done = json.loads((tmp_path / "2026-07-16" / "t1_review" / "done.json").read_text("utf-8"))
    assert done["mode"] == "deterministic"
    assert t1.pending_pairs(today="2026-07-17", scan_root=tmp_path,
                            cal=["20260716", "20260717"]) == []    # done 后不再 pending


def test_build_and_stage_pack_shape(tmp_path):
    _mk_scan(tmp_path)
    pack = t1.build_and_stage("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    assert pack["n"] == 3 and pack["t1"] == "2026-07-17"
    assert {r["code"] for r in pack["rows"]} == {"600001", "000062", "300100"}
    r6 = next(r for r in pack["rows"] if r["code"] == "600001")
    assert r6["verdict"] == "准" and r6["excess_pct"] == pytest.approx(4.83, abs=0.02)
    json.dumps(pack)                                               # 整包可序列化(NaN 已清)
    assert (tmp_path / "2026-07-16" / "t1_review" / "build_meta.json").exists()


# ───────────────── 自我迭代腿(2026-07-17:候选账本 → 注入 → 自动立案) ─────────────────


def test_upsert_candidates_merges_by_key_and_dedupes_days(tmp_path):
    cp = tmp_path / "cand.jsonl"
    t1.upsert_candidates("2026-07-16", [{"key": "beta-strip", "text": "先剔β再归因"}], path=cp)
    t1.upsert_candidates("2026-07-16", [{"key": "beta-strip", "text": "先剔β再归因"}], path=cp)  # 同日重跑幂等
    t1.upsert_candidates("2026-07-17", [{"key": "beta-strip", "text": "剔β归因 v2"},
                                        {"key": "", "text": "无key丢弃"}], path=cp)
    recs = t1.load_candidates(cp)
    assert len(recs) == 1 and recs[0]["days"] == ["2026-07-16", "2026-07-17"]
    assert recs[0]["texts"] == ["先剔β再归因", "剔β归因 v2"]


def test_promote_candidates_threshold_and_no_refile(tmp_path):
    """≥2 个 T 日才自动立案;立案回写 filed_pr 后不重复起草。"""
    cp = tmp_path / "cand.jsonl"
    filed_calls = []

    def fake_add(**kw):
        filed_calls.append(kw)
        return {"id": f"pr_test_{len(filed_calls):03d}"}

    t1.upsert_candidates("2026-07-16", [{"key": "beta-strip", "text": "x"}], path=cp)
    assert t1.promote_candidates(path=cp, add_proposal=fake_add) == []       # n_days=1 不立案
    t1.upsert_candidates("2026-07-17", [{"key": "beta-strip", "text": "x"}], path=cp)
    assert t1.promote_candidates(path=cp, add_proposal=fake_add) == ["pr_test_001"]
    assert "T1快环" in filed_calls[0]["summary"] and filed_calls[0]["kind"] == "prompt_rule"
    assert t1.promote_candidates(path=cp, add_proposal=fake_add) == []       # 已立案不重复
    assert t1.load_candidates(cp)[0]["filed_pr"] == "pr_test_001"


def test_render_t1_calibration_block(tmp_path):
    lp, cp = tmp_path / "ledger.jsonl", tmp_path / "cand.jsonl"
    assert t1.render_t1_calibration_block(path=lp, cand_path=cp) == ""       # 双空=零字节 parity
    _mk_scan(tmp_path)
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    t1.append_ledger(res, diagnoses={"600001": {"mechanism": "卡内论点兑现", "why": "w"}}, path=lp)
    t1.upsert_candidates("2026-07-16", [{"key": "beta-strip", "text": "先剔β再归因"}], path=cp)
    blk = t1.render_t1_calibration_block(path=lp, cand_path=cp)
    assert "T+1 快环校准" in blk and "数据非指令" in blk
    assert "卡内论点兑现×1" in blk and "n=1 日,观察中" in blk and "先剔β再归因" in blk


def test_finalize_ingests_candidates_and_promotes(tmp_path):
    """finalize 全链:candidates.json → 候选账本 → 已有 1 日历史时今日并入即触发自动立案。"""
    _mk_scan(tmp_path)
    lp, cp = tmp_path / "ledger.jsonl", tmp_path / "cand.jsonl"
    t1.upsert_candidates("2026-07-15", [{"key": "beta-strip", "text": "旧日观察"}], path=cp)
    t1.build_and_stage("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    rd = tmp_path / "2026-07-16" / "t1_review"
    (rd / "report.md").write_text("# r\n", encoding="utf-8")
    (rd / "candidates.json").write_text(json.dumps(
        [{"key": "beta-strip", "text": "今日再现"}]), encoding="utf-8")
    def fake(**kw):
        return {"id": "pr_test_001"}

    s = t1.finalize("2026-07-16", scan_root=tmp_path, ledger_path=lp,
                    cand_path=cp, add_proposal=fake)
    assert s["promoted"] == ["pr_test_001"]
    assert t1.load_candidates(cp)[0]["days"] == ["2026-07-15", "2026-07-16"]


def test_build_pack_carries_agents_cfg_and_open_candidates(tmp_path, monkeypatch):
    """pack 透传 agents 配置与既有候选(workflow 消费;schema 剪键的教训=键必须显式在场)。"""
    monkeypatch.setattr(t1, "_CAND_LEDGER", tmp_path / "cand.jsonl")
    t1.upsert_candidates("2026-07-16", [{"key": "beta-strip", "text": "x"}])
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"agents": {"t1_diag": {"model": "sonnet"}}})
    _mk_scan(tmp_path)
    pack = t1.build_and_stage("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    assert pack["agents_cfg"] == {"t1_diag": {"model": "sonnet"}}
    assert pack["open_candidates"] == [{"key": "beta-strip", "n_days": 1, "text": "x", "filed_pr": None}]


# ───────────── v2 尺(2026-07-17 调研落地:行业中性 + 截面稳健 z + 分诊) ─────────────


def test_verdict_z_double_gate():
    """z 路径双门:|z|≥0.5 且 |超额|≥0.8pp 才判方向;z 缺 → legacy pp 阈。"""
    assert t1.verdict("Overweight", 0.02, z=1.0) == "准"
    assert t1.verdict("Overweight", 0.02, z=0.3) == "中性"        # z 门不过
    assert t1.verdict("Overweight", 0.005, z=1.0) == "中性"       # pp 地板不过
    assert t1.verdict("Underweight", -0.02, z=-0.8) == "准"
    assert t1.verdict("Underweight", 0.02, z=0.8) == "不准"
    assert t1.verdict("Hold", 0.05, z=2.0) == "—"
    assert t1.verdict("Overweight", 0.02, z=float("nan")) == "准"  # z NaN → legacy


def test_robust_sigma_mad():
    import pandas as pd
    r = pd.Series([0.0] * 15 + [0.01] * 15)                       # n=30,MAD=0.005
    assert t1._robust_sigma(r) == pytest.approx(1.4826 * 0.005)
    assert pd.isna(t1._robust_sigma(pd.Series([0.01] * 10)))      # n<30 → NaN(退 legacy)


def _prices_wide():
    """≥30 只全市场帧(含行业列):电子行业整体 +4%,里面 600001 +6% = 行业内超额 +2%。"""
    rows = [("600001", "电子", 0.060), ("000062", "电子", 0.040), ("300100", "电子", 0.040),
            ("600519", "白酒", 0.010)]
    rows += [(f"00{i:04d}", "其他", 0.001 * (i % 5 - 2)) for i in range(30)]   # 背景票
    df = pd.DataFrame(rows, columns=["code", "industry", "cc1"])
    df["close_t"] = 10.0
    df["close_t1"] = 10.0 * (1 + df["cc1"])
    df["open_t1"] = 10.0
    df["oc1"] = df["cc1"]
    df["hi_oc"] = df["cc1"] + 0.002
    return df


def test_build_v2_industry_neutral_z(tmp_path):
    """行业中性:电子整体 +4% 是板块共振,600001 行业内只 +2% → z 判定不再被板块β骗。"""
    _mk_scan(tmp_path)
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=_prices_wide(), cal=_CAL)
    row = res["scorecard"].set_index("code")
    assert res["sigma"] is not None
    e = row.loc["600001"]
    assert e["excess_ind"] == pytest.approx(0.060 - (0.060 + 0.040 + 0.040) / 3, abs=1e-9)
    assert abs(e["excess"]) > abs(e["excess_ind"])                # 市场超额虚高,行业内才是真 idio
    assert e["verdict"] in ("准", "中性")                          # 判定走 z 路径不炸
    assert "excess_ind" in res["scorecard"].columns and "z" in res["scorecard"].columns


def test_sealed_open_limit_board(tmp_path):
    """一字开盘板:hi_oc≈0 且 cc1≥板幅×0.98 → sealed=True(开盘买不到,不计可实现)。"""
    _mk_scan(tmp_path)
    pr = _prices()
    pr.loc[pr["code"] == "600001", ["cc1", "oc1", "hi_oc"]] = [0.0995, 0.0, 0.0]
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=pr, cal=_CAL)
    row = res["scorecard"].set_index("code")
    assert bool(row.loc["600001", "sealed"]) and not bool(row.loc["000062", "sealed"])


def test_needs_diag_triage(tmp_path):
    """分诊:不准/惊奇/方向票|z|≥1 才必诊;β/噪声区间不烧 token。z 缺(小样本)→ 全必诊兜底?
    否——needs_diag 由 verdict/surprise 驱动,legacy 路径下惊奇阈=3pp 仍生效。"""
    _mk_scan(tmp_path)
    res = t1.build_scorecard("2026-07-16", scan_root=tmp_path, prices=_prices(), cal=_CAL)
    row = res["scorecard"].set_index("code")
    assert bool(row.loc["300100", "needs_diag"])                  # UW 惊奇(legacy |超额|≥3pp)
    assert not bool(row.loc["000062", "needs_diag"])              # Hold 小超额 → 不烧


def test_calibration_block_stage_routing(tmp_path):
    """ERL 教训:相关性>数量——L3 只看 L3/gate/process/无标,L4 只看 L4/intel。"""
    lp, cp = tmp_path / "l.jsonl", tmp_path / "c.jsonl"
    t1.upsert_candidates("2026-07-16", [
        {"key": "a-l3", "text": "L3观察", "stage": "L3"},
        {"key": "b-l4", "text": "L4观察", "stage": "L4"},
        {"key": "c-none", "text": "无标观察"}], path=cp)
    b3 = t1.render_t1_calibration_block(path=lp, cand_path=cp, stage="L3")
    b4 = t1.render_t1_calibration_block(path=lp, cand_path=cp, stage="L4")
    assert "L3观察" in b3 and "无标观察" in b3 and "L4观察" not in b3
    assert "L4观察" in b4 and "L3观察" not in b4 and "无标观察" not in b4


def test_ledger_report_expectancy_and_conviction(tmp_path):
    """期望值(胜率×均赢/均亏,UW 顺方向)+ conviction 校准行;🔒sealed 不进可实现。"""
    lp = tmp_path / "l.jsonl"
    rows = [
        {"t": "2026-07-15", "t1": "2026-07-16", "code": "1", "rating": "Underweight",
         "conviction": 60, "excess_ind": -0.03, "excess": -0.03, "verdict": "准",
         "surprise": True, "sealed": False, "diagnosed": False},
        {"t": "2026-07-16", "t1": "2026-07-17", "code": "2", "rating": "Underweight",
         "conviction": 80, "excess_ind": 0.02, "excess": 0.02, "verdict": "不准",
         "surprise": False, "sealed": False, "diagnosed": False},
        {"t": "2026-07-16", "t1": "2026-07-17", "code": "3", "rating": "Overweight",
         "conviction": 75, "excess_ind": 0.05, "excess": 0.05, "verdict": "准",
         "surprise": True, "sealed": True, "diagnosed": False},   # 一字板 → 不进可实现
    ]
    import json as _json
    lp.write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    rep = t1.render_ledger_report(path=lp)
    assert "Underweight:n=2 准1/不准1" in rep
    assert "胜率 1/2,均赢 +3.00pp / 均亏 -2.00pp" in rep          # UW 顺方向:跌=赢
    assert "- Overweight" not in rep                               # 唯一 OW 是一字板 → 期望值行整行不出
    # conviction 校准看「判断」对错,sealed 不剔(识别对了只是买不到):>70 桶 = 不准(80)+准(75)
    assert "conviction 校准" in rep and "56-70: 1/1" in rep and ">70: 1/2" in rep
