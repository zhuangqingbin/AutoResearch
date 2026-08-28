"""结果账本(2026-08-26 §4.4):只记不学的推荐票事后读数。

判据围绕三件真实风险写:① 幂等(每晚重跑不得让「BUY n 笔」翻倍);② provenance 分列
(shadow 期的 BUY 明写「不执行」、读自共享 staging 的行未必是本 run 那份 —— 混读就是
把假战绩算进去);③ 口径与 `edge_census` 同源(两边读数要能直接对表)。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import outcome


# ───────────────────────── 夹具:一个最小的已发布 run ─────────────────────────

def _run(tmp_path, run_id="20260825_2149", date="2026-08-25", *, mode="active",
         buys=("603317",), in_mirror=True):
    run = tmp_path / ws.reports_root() / "scan" / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(
        {"analysis_date": date, "run_id": "20260825T123650212028Z"}), encoding="utf-8")
    base = run / "trace" / "staging" if in_mirror else tmp_path / ws.scan_root() / date
    base.mkdir(parents=True)
    (base / "finalists.csv").write_text(
        "code,name,sector,lane,guard,conviction\n"
        "603317,天味食品,调味发酵品Ⅱ,reversion,,55\n"
        "300857,协创数据,消费电子,pinned,,57\n", encoding="utf-8")
    (base / "_final_ratings.json").write_text(
        json.dumps({"603317": "Underweight", "300857": "Underweight"}), encoding="utf-8")
    (base / "_early_stop.json").write_text(
        json.dumps({"603317": {"phase": "P3", "reason": "资金流出"}}), encoding="utf-8")
    (base / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": mode, "rule_version": "e6.v2.0", "blocked": False,
        "buys": [{"code": c, "rank": 1, "basis": "relative"} for c in buys],
        "candidates": [{"code": "603317", "eligible": True, "rank": 2}],
    }), encoding="utf-8")
    return run


def _fake_market(monkeypatch, *, gap=0.01, pct1=-2.0, pos=0.3):
    """把湖那一层换成合成帧 —— 本测试要验的是账本逻辑,不是取数。"""
    codes = ["603317", "300857", "000001", "000002"]
    fr = pd.DataFrame(index=pd.Index(codes, name="code"))
    fr[outcome.MAIN] = [gap, 0.02, 0.0, -0.01]
    fr["buyable_c1"] = True
    fr["fwd_5_oc"] = 0.03
    fr["fwd_10_oc"] = 0.05
    fr["t1_open"] = 10.0
    fr["t1_high"] = 11.0
    fr["t1_low"] = 9.0
    fr["t1_close"] = 9.0 + 2.0 * pos
    fr["t1_pct_chg"] = pct1
    fr["t2_open"] = 10.5
    fr["t1_pos_in_range"] = pos
    monkeypatch.setattr(outcome, "market_frame",
                        lambda date, **k: (fr, {"t1": "20260826", "t2": "20260827",
                                                "n": len(fr)}))
    return fr


# ───────────────────────── 幂等 ─────────────────────────

def test_upsert_is_idempotent(tmp_path, monkeypatch):
    """每晚 prelude 都会跑一次 fill;重复 append 会让「BUY n 笔」直接翻倍。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    run = _run(tmp_path)
    doc = outcome.compute_outcome(run)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    outcome.upsert_ledger(doc, root)
    outcome.upsert_ledger(doc, root)
    rows = outcome.load_ledger(root)
    assert len(rows) == 2                      # 两只票,不是六行
    assert len({(r["run_id"], r["code"]) for r in rows}) == 2


def test_fill_skips_already_complete_runs(tmp_path, monkeypatch):
    """增量:成熟的 run 不重算 —— 否则每天成本随历史长度线性增长。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    _run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    first = outcome.fill(reports_root=root)
    second = outcome.fill(reports_root=root)
    assert first["filled"] == 1 and second["filled"] == 0 and second["skipped"] == 1


def test_immature_run_is_skipped_not_written(tmp_path, monkeypatch):
    """D+2 还没落湖 = **状态不是故障**:不写半截结果,留着下次再算。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(outcome, "market_frame", lambda date, **k: (None, {"reason": "未成熟"}))
    _run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    res = outcome.fill(reports_root=root)
    assert res["filled"] == 0 and res["skipped"] == 1
    assert not outcome.outcome_path("20260825_2149", root).exists()
    assert outcome.load_ledger(root) == []


# ───────────────────────── provenance 分列 ─────────────────────────

def test_shadow_buys_are_not_counted_as_active(tmp_path, monkeypatch):
    """shadow 期的 BUY 在当天报告里明写「非正式·不执行」。把它算进战绩 = 自欺。

    实测背景:2026-08-13/08-18 两天 brief 印的是 BLOCKED,而共享 staging 的决策文件被后来
    的影子回放改写成 `buys=[688766]`(还是一只 📌 持仓)。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(
        _run(tmp_path, "20260818_2043", "2026-08-18", mode="shadow")), root)
    line = outcome.ledger_line(root)
    assert "active BUY 0 笔" in line and "shadow 期 1 笔不计" in line


def test_src_column_flags_shared_staging_reads(tmp_path, monkeypatch):
    """读自共享 staging 的行必须自带标记 —— 同数据日重跑会覆盖它。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    run = _run(tmp_path, in_mirror=False)          # 只有共享 staging,没有 trace/staging
    doc = outcome.compute_outcome(run)
    assert doc["read_from_shared_staging"] is True
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    assert {r["src"] for r in outcome.load_ledger(root)} == {"shared"}


def test_mirrored_run_is_marked_src_run(tmp_path, monkeypatch):
    """有 `trace/staging` 的 run 读自己那份 —— 这正是本波镜像存在的意义。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["read_from_shared_staging"] is False
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(doc, root)
    assert {r["src"] for r in outcome.load_ledger(root)} == {"run"}


# ───────────────────────── 角色与执行线 ─────────────────────────

def test_roles_distinguish_buy_pinned_finalist(tmp_path, monkeypatch):
    """📌 保送不是系统选的(2026-07-16 判例:保送不算判例),必须与 finalist 分账。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["role"] == "BUY"
    assert doc["rows"]["300857"]["role"] == "pinned"


@pytest.mark.parametrize("pct1,pos,expect", [
    (-2.0, 0.30, True),     # 不追高、收得弱 → 可执行
    (7.2, 0.93, False),     # 当日大涨 + 收在区间顶 → 正是四年逐年为负的那一档
    (1.0, 0.85, False),     # 涨幅达标但收在区间上 30%
    (5.0, 0.20, False),     # 收得弱但当日涨超 3%
])
def test_exec_ok_encodes_the_a4_line(tmp_path, monkeypatch, pct1, pos, expect):
    """A4 执行线:当日涨幅 ≤3% ∧ 收盘不在区间上 30% ∧ 未封涨停。
    这是**量尺不是门** —— 先记下来,才有「BUY 无条件」与「BUY ∧ 执行线」两条曲线可比。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch, pct1=pct1, pos=pos)
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["exec_ok"] is expect


def test_exec_ok_is_none_when_unbuyable_unknown(tmp_path, monkeypatch):
    """封涨停买不进 → 执行线不成立(不是「未知按可执行」)。"""
    monkeypatch.chdir(tmp_path)
    fr = _fake_market(monkeypatch)
    fr["buyable_c1"] = False
    doc = outcome.compute_outcome(_run(tmp_path))
    assert doc["rows"]["603317"]["exec_ok"] is False


# ───────────────────────── 口径 ─────────────────────────

def test_relative_columns_use_declared_denominators(tmp_path, monkeypatch):
    """三个相对列分母不同,**列名即口径**;混读会得到符号相反的结论(均值 vs 中位)。"""
    fr = pd.DataFrame({outcome.MAIN: [0.10, 0.00, 0.00, -0.02],
                       "buyable_c1": True},
                      index=["a", "b", "c", "d"])
    rel = outcome._relative_columns(fr, {"a": "钢铁", "b": "钢铁", "c": "银行", "d": "银行"})
    base = fr[outcome.MAIN]
    assert rel.loc["a", "rel_gap_market"] == pytest.approx(0.10 - base.mean())
    assert rel.loc["a", "excess_med_market"] == pytest.approx(0.10 - base.median())
    assert rel.loc["a", "rel_gap_sector"] == pytest.approx(0.10 - np.mean([0.10, 0.00]))


def test_sector_relative_is_nan_without_industry(tmp_path):
    """没有行业 → 行业相对列是 NaN,**不猜**(同 ruler.REL_SECTOR 的既定语义)。"""
    fr = pd.DataFrame({outcome.MAIN: [0.01, 0.02], "buyable_c1": True}, index=["a", "b"])
    rel = outcome._relative_columns(fr, {"a": "", "b": ""})
    assert pd.isna(rel.loc["a", "rel_gap_sector"])


def test_ledger_line_withholds_mean_below_min_n(tmp_path, monkeypatch):
    """<20 笔只印「攒样本」不印均值 —— 小样本均值会被当成结论读,而这条线存在的意义
    正是不让人再凭印象说「最近推荐得挺准」。"""
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    root = tmp_path / ws.reports_root() / "scan"
    outcome.upsert_ledger(outcome.compute_outcome(_run(tmp_path)), root)
    line = outcome.ledger_line(root)
    assert "攒样本 1/20" in line and "均 gap" not in line


def test_ledger_line_empty_is_honest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "空" in outcome.ledger_line(tmp_path / ws.reports_root() / "scan")


def test_cli_fill_and_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _fake_market(monkeypatch)
    _run(tmp_path)
    assert outcome.main(["fill"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0])["filled"] == 1
    assert outcome.main(["line"]) == 0
    capsys.readouterr()
