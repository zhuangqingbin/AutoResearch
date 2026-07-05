"""陷阱预旗 v1:质押旗(L4 简报)+ 监管旗(L3 表)。advisory 档,默认关=parity。合成,无网络。

spec: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §5
关键约束:不动 news_digest key 集合 / _EVENT_TAGS(情感口径零变化,契约测试冻结);
监管检测走独立 reg_hits;质押阈值单一事实源在 scoring(与 tushare_enrich 深核共用)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.agents.l3_select import l3_table_md
from autoresearch.scan.agents.l4_card import compose_funnel_brief

_DATE = "2026-07-03"


def _row(code, name="甲"):
    return {"code": code, "name": name, "industry": "电子", "composite": 80.0,
            "main_net_ratio": 0.14, "main_inflow_yi": 0.91, "cmf_20": 0.05,
            "pct_60d": 10.0, "pe": 30.0}


def _mk(root, rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


# ---------------- 质押旗(阈值单一事实源 + 简报注入) ----------------

def test_pledge_flag_label_thresholds():
    from autoresearch.common.scoring import pledge_flag_label
    assert pledge_flag_label(45.0) == "爆雷红旗"
    assert pledge_flag_label(25.0) == "偏高"
    assert pledge_flag_label(5.0) == ""
    assert pledge_flag_label(None) == ""
    assert pledge_flag_label(float("nan")) == ""


def test_funnel_brief_pledge_mark_presence_gated(tmp_path):
    d = _mk(tmp_path, [_row("000001")])
    pd.DataFrame([_row("000001")]).to_csv(d / "L1_recall_top1000.csv", index=False)
    assert "高质押" not in compose_funnel_brief("000001", d)          # 无 pledge.csv → 不注
    pd.DataFrame([{"code": "000001", "pledge_ratio": 45.0, "end_date": "20260630"}]
                 ).to_csv(d / "pledge.csv", index=False)
    brief = compose_funnel_brief("000001", d)
    assert "⚠高质押(爆雷红旗" in brief and "45.0%" in brief and "平仓线" in brief
    pd.DataFrame([{"code": "000001", "pledge_ratio": 5.0, "end_date": "20260630"}]
                 ).to_csv(d / "pledge.csv", index=False)
    assert "质押" not in compose_funnel_brief("000001", d)            # 低比例 → 不加噪


def test_fetch_pledge_reuses_recent_days(tmp_path):
    from autoresearch.scan.agents.l4_card import fetch_pledge
    old = tmp_path / "2026-07-01"
    old.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "pledge_ratio": 30.0, "end_date": "20260628"}]
                 ).to_csv(old / "pledge.csv", index=False)
    today = tmp_path / _DATE
    today.mkdir()
    pd.DataFrame([{"code": "000001"}, {"code": "000002"}]).to_csv(
        today / "finalists.csv", index=False)
    calls = []

    def fake_fetch(code6):
        calls.append(code6)
        return (55.0, "20260630")

    df = fetch_pledge(today, fetch_fn=fake_fetch)
    assert calls == ["000002"]                                        # 000001 复用 2 日前行
    got = df.set_index("code")
    assert got.at["000001", "pledge_ratio"] == 30.0
    assert got.at["000002", "pledge_ratio"] == 55.0
    assert (today / "pledge.csv").exists()

    stale = tmp_path / "2026-06-20"                                    # 超 7 日 → 不复用
    stale.mkdir()
    pd.DataFrame([{"code": "000003", "pledge_ratio": 60.0, "end_date": "20260601"}]
                 ).to_csv(stale / "pledge.csv", index=False)
    day2 = tmp_path / "2026-07-04"
    day2.mkdir()
    pd.DataFrame([{"code": "000003"}]).to_csv(day2 / "finalists.csv", index=False)
    calls.clear()
    fetch_pledge(day2, fetch_fn=fake_fetch)
    assert calls == ["000003"]


# ---------------- 监管旗(独立检测器 + L3 表列,默认关=parity) ----------------

def test_reg_hits_detector():
    from autoresearch.scan.agents.l3_news import reg_hits
    hits = reg_hits(["关于收到证监会立案告知书的公告", "日常经营合同公告"])
    assert "立案" in hits and "证监会" in hits
    assert reg_hits(["2026年半年度业绩预增公告"]) == ""
    assert reg_hits([]) == ""


def test_l3_table_reg_flag_on(tmp_path):
    d = _mk(tmp_path, [_row("000001"), _row("000002", name="乙")])
    nd = d / "L3_news"
    nd.mkdir()
    (nd / "000001.json").write_text(json.dumps(
        [{"title": "关于收到交易所监管工作函的公告", "ann_date": "20260701"}],
        ensure_ascii=False), encoding="utf-8")
    (nd / "000002.json").write_text(json.dumps(
        [{"title": "中标重大项目公告", "ann_date": "20260701"}], ensure_ascii=False),
        encoding="utf-8")
    md = l3_table_md(_DATE, root=tmp_path, reg_flag=True)
    assert "news_reg" in md and "监管工作函"[:2] in md                # 列 + 命中词入表
    assert "⚠监管" in md and "显式回应" in md                        # 图例禁则
    assert "情感列口径不变" in md                                     # 声明:非词表变更


def test_l3_table_reg_flag_default_off_parity(tmp_path):
    d = _mk(tmp_path, [_row("000001")])
    nd = d / "L3_news"
    nd.mkdir()
    (nd / "000001.json").write_text(json.dumps(
        [{"title": "收到证监会立案告知书", "ann_date": "20260701"}], ensure_ascii=False),
        encoding="utf-8")
    md = l3_table_md(_DATE, root=tmp_path)
    assert "news_reg" not in md and "⚠监管" not in md
