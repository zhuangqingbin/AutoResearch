"""K4 回归 —— run 分区下三个跨日读者只看得见本 run 自己的日期。

病灶(spec docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md §2.2 K4):
法证 capsule 波把 staging 改成 `context_<engine>/scan_runs/<run_id>/staging/<date>/`,
分区与否由进程环境变量 `AUTORESEARCH_RUN_ID` 决定(`common/workspace.py:105-107,121-122`);
而 `sector/reuse.find_reusable`、`scan/l3/prompt._prev_l3_day`、`scan/menu.zero_buy_streak`
仍在遍历 `ws.scan_root()` 的**兄弟目录** —— 三处都**静默**降级(复用永远落空、Δ 模式永远
退化成全量、0 买连败永远数成 0)。

本文件的后三条(消费者活体)才是「把实现删掉会红」的探针:去掉 `published_days` 接线,
三个读者在 run 分区下分别退回 {} / None / 0。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import published_days

RUN_ID = "20260826T120000000000Z"
TODAY = "2026-08-26"


@pytest.fixture
def partitioned(tmp_path, monkeypatch):
    """一个活跃 run:`scan_root()` = tmp 的 run 分区,历史根 = tmp 的 `context_claude/scan`。"""
    ctx = tmp_path / "context_claude"
    rpt = tmp_path / "reports_claude"
    monkeypatch.setattr(ws, "context_root", lambda: ctx)
    monkeypatch.setattr(ws, "reports_root", lambda: rpt)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", RUN_ID)
    (ctx / "scan_runs" / RUN_ID / "staging" / TODAY).mkdir(parents=True)
    return ctx, rpt


def _ledger(rpt, dates, run_ids=None):
    v = rpt / "scan" / "_ledger" / "views"
    v.mkdir(parents=True, exist_ok=True)
    head = "engine,capsule_run_id,report_dir_id,run_local_date,analysis_date,business_status"
    rows = [f"claude,{(run_ids or {}).get(d, '')},{i},{d},{d},SUCCEEDED"
            for i, d in enumerate(dates)]
    (v / "runs.csv").write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")


def _manifest(rpt, dir_id, date):
    p = rpt / "scan" / dir_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "manifest.json").write_text(json.dumps({"analysis_date": date}), encoding="utf-8")


def _legacy_day(ctx, date):
    d = ctx / "scan" / date
    d.mkdir(parents=True, exist_ok=True)
    return d


# ═══════ 解析器本体 ═══════

def test_previous_scan_days_survives_run_partition(partitioned):
    """run 分区下 scan_root() 的兄弟目录只有本 run 的日期 —— 昨天必须从已发布账本找。"""
    ctx, rpt = partitioned
    _ledger(rpt, ["2026-08-24", "2026-08-25", TODAY])
    assert sorted(p.name for p in ws.scan_root().iterdir()) == [TODAY]   # 现场:只有今天
    assert published_days.previous_scan_days(TODAY) == ["2026-08-25", "2026-08-24"]
    assert published_days.resolve(TODAY).source == published_days.SOURCE_LEDGER


def test_falls_back_to_manifests_when_ledger_view_missing(partitioned):
    ctx, rpt = partitioned
    _manifest(rpt, "20260824_2102", "2026-08-24")
    _manifest(rpt, "20260825_2149", "2026-08-25")
    _manifest(rpt, "20260826_2120", TODAY)                              # 今天本身要排除
    assert published_days.previous_scan_days(TODAY) == ["2026-08-25", "2026-08-24"]
    assert published_days.resolve(TODAY).source == published_days.SOURCE_MANIFEST


def test_falls_back_to_legacy_root_without_ledger(partitioned):
    ctx, rpt = partitioned
    for d in ("2026-08-24", "2026-08-25", TODAY):
        _legacy_day(ctx, d)
    assert published_days.previous_scan_days(TODAY) == ["2026-08-25", "2026-08-24"]
    assert published_days.resolve(TODAY).source == published_days.SOURCE_LEGACY


def test_previous_staging_dirs_prefers_current_run_then_legacy(partitioned):
    ctx, rpt = partitioned
    _ledger(rpt, ["2026-08-24", "2026-08-25"])
    _legacy_day(ctx, "2026-08-24")
    _legacy_day(ctx, "2026-08-25")
    (ws.scan_root() / "2026-08-25").mkdir(parents=True)                 # 本 run 也有 08-25
    dirs = published_days.previous_staging_dirs(TODAY)
    assert [p.name for p in dirs] == ["2026-08-25", "2026-08-24"]
    assert dirs[0] == ws.scan_root() / "2026-08-25"                     # 本 run 优先
    assert dirs[1] == ctx / "scan" / "2026-08-24"                       # 回落历史根


def test_previous_staging_dirs_locates_other_run_partitions(partitioned):
    """账本记了 run_id、日期键历史根已不产 —— 仍要能定位到那个 run 的 staging。"""
    ctx, rpt = partitioned
    other = "20260825T120000000000Z"
    _ledger(rpt, ["2026-08-25"], run_ids={"2026-08-25": other})
    (ctx / "scan_runs" / other / "staging" / "2026-08-25").mkdir(parents=True)
    dirs = published_days.previous_staging_dirs(TODAY)
    assert dirs == [ctx / "scan_runs" / other / "staging" / "2026-08-25"]


def test_bad_date_and_empty_workspace_are_quiet(partitioned):
    assert published_days.previous_scan_days("not-a-date") == []
    assert published_days.previous_staging_dirs("not-a-date") == []
    assert published_days.resolve(TODAY).source == published_days.SOURCE_NONE
    assert published_days.previous_staging_dirs(TODAY) == []


def test_limit_caps_both_functions(partitioned):
    ctx, rpt = partitioned
    days = [f"2026-08-{d:02d}" for d in range(1, 26)]
    _ledger(rpt, days)
    for d in days:
        _legacy_day(ctx, d)
    assert published_days.previous_scan_days(TODAY, limit=3) == ["2026-08-25", "2026-08-24",
                                                                "2026-08-23"]
    assert len(published_days.previous_staging_dirs(TODAY, limit=3)) == 3


# ═══════ 三个消费者(活体回归探针)═══════

_BRIEF = """# 行业 brief — 半导体 @ 2026-08-25

## 地形段(喂 L3/L4 · 描述性)
- 景气读数:中位60日 5.0%
"""


def _sector_day(d, mom, *, regime="range", with_brief=False):
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "600001", "industry": "半导体", "pct_60d": mom},
                  {"code": "600002", "industry": "半导体", "pct_60d": mom}]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    if with_brief:
        (d / "sector_briefs").mkdir(exist_ok=True)
        (d / "sector_briefs" / "半导体.md").write_text(_BRIEF, encoding="utf-8")
    return d


def test_sector_reuse_finds_yesterday_across_run_partition(partitioned):
    """TTL 复用:昨天的 brief 在历史根,今天在 run 分区 —— 必须还能复用(否则白付 6 个 opus)。"""
    from autoresearch.sector.reuse import find_reusable

    ctx, rpt = partitioned
    _ledger(rpt, ["2026-08-25"])
    _sector_day(ctx / "scan" / "2026-08-25", 5.0, with_brief=True)
    _sector_day(ws.scan_root() / TODAY, 6.0)
    found = find_reusable(TODAY, ["半导体"])                            # 不传 root = 生产路径
    assert found["半导体"]["prev"] == "2026-08-25"
    assert found["半导体"]["src"].endswith("2026-08-25/sector_briefs/半导体.md")


def _l3_day(d, codes):
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": c, "name": c, "composite": 50.0, "pct_60d": 10.0}
                  for c in codes]).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame([{"code": c} for c in codes]).to_csv(d / "L3_judged_full.csv", index=False)
    return d


def test_prev_l3_day_found_across_run_partition(partitioned):
    """Δ 模式:前日 L3 现场在历史根 —— run 分区下也必须找得到,不能静默退回全量表。"""
    from autoresearch.scan.l3.prompt import _prev_l3_day

    ctx, rpt = partitioned
    _ledger(rpt, ["2026-08-24", "2026-08-25"])
    _l3_day(ctx / "scan" / "2026-08-24", ["000001"])
    _l3_day(ctx / "scan" / "2026-08-25", ["000001", "000002"])
    (ctx / "scan" / "2026-08-21").mkdir(parents=True)                   # 无两件 csv → 判据不变
    prev = _prev_l3_day(TODAY)
    assert prev is not None and prev.name == "2026-08-25"


def _card_day(d, ratings):
    (d / "details").mkdir(parents=True, exist_ok=True)
    for code, rating in ratings.items():
        (d / "details" / f"{code}.md").write_text(f"# {code}\n**评级**: {rating}\n",
                                                  encoding="utf-8")
    pd.DataFrame([{"code": c, "name": c, "rating": r} for c, r in ratings.items()]).to_csv(
        d / "finalists.csv", index=False)
    return d


def test_zero_buy_streak_counts_legacy_days_under_run_partition(partitioned, monkeypatch):
    """0 买连败是 L4 预算五面旗之一 —— run 分区下不能永远数成 0。"""
    from autoresearch.scan.menu import zero_buy_streak

    monkeypatch.setattr("autoresearch.scan.relative_buy.is_active", lambda: False)
    ctx, rpt = partitioned
    _ledger(rpt, ["2026-08-21", "2026-08-24", "2026-08-25"])
    _card_day(ctx / "scan" / "2026-08-25", {"000001": "Hold"})
    _card_day(ctx / "scan" / "2026-08-24", {"000002": "Hold"})
    _card_day(ctx / "scan" / "2026-08-21", {"000003": "Overweight"})    # 有买 → 断链
    assert zero_buy_streak(ws.scan_root() / TODAY) == 2
