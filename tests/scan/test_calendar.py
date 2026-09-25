"""日历(解禁/预约披露):quarter 端点、flags、section、简报注入、assemble 嵌入。合成,无网络。

spec: docs/specs/2026-07-02-scan-calendar-shadow-design.md §1
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.calendar import _last_quarter_end, calendar_flags, calendar_section

_ROWS = [
    {"code": "000001", "kind": "unlock", "event_date": "20260715", "detail": "定增股份·3方", "ratio": 8.2},
    {"code": "000001", "kind": "disclosure", "event_date": "20260716", "detail": "预约披露(期 20260630)", "ratio": None},
    {"code": "000002", "kind": "unlock", "event_date": "20260710", "detail": "定增股份·1方", "ratio": 0.5},
    {"code": "000003", "kind": "unlock", "event_date": "20261230", "detail": "首发原股东", "ratio": 30.0},
]


def _mk(tmp_path, date="2026-07-02", finalists=("000001",)):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(_ROWS).to_csv(d / "calendar.csv", index=False)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in finalists]).to_csv(
        d / "finalists.csv", index=False)
    return d


def test_last_quarter_end():
    assert _last_quarter_end("2026-07-02") == "20260630"
    assert _last_quarter_end("2026-02-01") == "20251231"
    assert _last_quarter_end("2026-04-01") == "20260331"


def test_calendar_flags(tmp_path):
    d = _mk(tmp_path)
    f1 = calendar_flags(d, "000001")
    assert any("解禁" in x and "8.2%" in x for x in f1)
    assert any("预约披露" in x and "20260716" in x for x in f1)
    assert calendar_flags(d, "000002") == []          # ratio 0.5 < 2.0 阈,不旗
    assert calendar_flags(d, "000003") == []          # 解禁在 30 日窗外
    assert calendar_flags(tmp_path / "nope", "000001") == []


def test_calendar_section(tmp_path):
    d = _mk(tmp_path)
    s = calendar_section(d)
    assert "📅" in s and "000001 20260716" in s        # finalists 披露
    assert "8%" in s or "8.2" in s or "(8%)" in s      # 大解禁 ≥5%
    assert "000003" not in s                           # 14 日窗外
    assert calendar_section(tmp_path / "nope") == ""


def test_brief_injects_calendar(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk(tmp_path)
    s = compose_funnel_brief("000001", d)
    assert "解禁" in s and "预约披露" in s
    s2 = compose_funnel_brief("000002", d)
    assert "解禁" not in s2                            # 小解禁不旗


def test_assemble_embeds_calendar(tmp_path):
    from autoresearch.scan.assemble import build_summary
    d = _mk(tmp_path)
    (d / "meta.json").write_text("{}", encoding="utf-8")
    md = build_summary(d, "2026-07-02", "1200", "20260702_1200")
    # 2026-08-29 B+ 重构:节标题由 summary 自己出(生产者自带的 `### 📅 …日历` 内标题被剥,
    # 避免与本节标题重复);断言改锚新标题 **并加验真日历内容**——只对标题断言是弱验收。
    assert "## 📅 未来 14 天" in md
    assert "000001 20260716" in md                     # 披露锚真进了 summary,不是只剩个标题
    assert "000003" not in md                          # 14 日窗外的仍被挡在外面
    assert "### 📅" not in md                          # 内标题已剥,不与节标题重复


# ───────────────────────── 第三腿:指数调样(2026-09-25 §2.3) ─────────────────────────
from autoresearch.scan import index_events as ie  # noqa: E402


class _DeadPro:
    """解禁/披露两腿离线:方法一律抛 → harvest_calendar 那两段按既有 try/except 静默跳过。"""

    def share_float(self, **kw):
        raise RuntimeError("offline")

    def disclosure_date(self, **kw):
        raise RuntimeError("offline")


_EV = pd.DataFrame([
    {"code": "000001", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    {"code": "999999", "index_code": "000905", "index_name": "中证500", "side": "drop", "ann_date": "20260529",
     "eff_close_date": "20260612", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": 0.4},
    {"code": "000002", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20260909",
     "eff_close_date": None, "phase": "unknown_eff", "source": "none", "flow_adv_days": None},
], columns=ie.EVENT_COLS)


def _offline(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    from autoresearch.scan import calendar as cal
    monkeypatch.setattr(ts_src, "_pro", lambda: _DeadPro())
    monkeypatch.setattr(cal, "knob", lambda block, key, cli, default, cfg=None: default if cli is None else cli)


def test_harvest_calendar_third_leg_is_off_by_default(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def must_not_run(*a, **k):
        raise AssertionError("index_events must not be harvested when the knob is off")
    monkeypatch.setattr(ie, "harvest_index_events", must_not_run)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path)
    assert df.empty and (tmp_path / "2026-06-11" / "calendar.csv").exists()
    assert not (tmp_path / "2026-06-11" / "index_events.csv").exists()


def test_harvest_calendar_third_leg_filters_to_wanted_codes_and_keeps_phase(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)

    def fake_harvest(date, outdir, **k):
        ie.write_index_events(outdir, _EV)
        return _EV
    monkeypatch.setattr(ie, "harvest_index_events", fake_harvest)
    df = cal.harvest_calendar("2026-06-11", {"000001", "000002"}, root=tmp_path, index_rebalance=True)
    rows = df[df["kind"] == "index_rebalance"]
    assert rows["code"].tolist() == ["000001"]                    # 999999 不在 want;000002 无生效日不进日历
    assert rows.iloc[0]["event_date"] == "20260612"
    assert rows.iloc[0]["detail"] == "沪深300 调入|passive_close_eve"
    assert pd.isna(rows.iloc[0]["ratio"])
    assert (tmp_path / "2026-06-11" / "index_events.csv").exists()   # 全量表照落(999999 也在里面)
    assert len(ie.load_index_events(tmp_path / "2026-06-11")) == 3


def test_harvest_calendar_source_absent_leaves_other_legs_intact(tmp_path, monkeypatch):
    from autoresearch.scan import calendar as cal
    _offline(monkeypatch)
    monkeypatch.setattr(ie, "harvest_index_events", lambda date, outdir, **k: None)
    df = cal.harvest_calendar("2026-06-11", {"000001"}, root=tmp_path, index_rebalance=True)
    assert df.empty and not (tmp_path / "2026-06-11" / "index_events.csv").exists()
