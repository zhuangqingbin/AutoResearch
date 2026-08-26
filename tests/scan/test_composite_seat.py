"""L3 守卫⑨ composite 证据席(2026-08-26 §3 路A · A1)。

**它要挡的病**:E6 此前只在 L3 finalist 里挑,而 finalist 这一族在隔夜主尺上 40 日相对超额
−0.27pp(t=−3.94)显著为负;全表唯一正证据是确定性 composite(+0.14pp,t=3.05)。席位把
BUY 的候选来源从判断层搬到证据层。

判据顺序 = 风险顺序:① 席位真的到货(不是又一条「死了也像活着」的腿);② 剔除规则(追高/
ST/📌)真的咬得动;③ L3 判过的票必须把判断带过来(pinned 当年整段丢弃的同款事故);
④ 关掉 = 逐字 parity。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.l3 import merge, triage


def _l2(rows):
    return pd.DataFrame(rows)


_MENU = [
    {"code": "000001", "name": "甲", "industry": "银行", "gbdt_score": 71.2, "pct_1d": 0.5, "pinned": False},
    {"code": "000002", "name": "乙", "industry": "半导体", "gbdt_score": 70.5, "pct_1d": -2.4, "pinned": False},
    {"code": "000003", "name": "丙", "industry": "能源", "gbdt_score": 70.3, "pct_1d": 10.0, "pinned": False},
    {"code": "000004", "name": "*ST丁", "industry": "化工", "gbdt_score": 70.2, "pct_1d": 1.0, "pinned": False},
    {"code": "000005", "name": "戊", "industry": "食品", "gbdt_score": 70.1, "pct_1d": 1.0, "pinned": True},
    {"code": "000006", "name": "己", "industry": "军工", "gbdt_score": 69.9, "pct_1d": 1.0, "pinned": False},
]


def test_seats_are_the_top_composite_names(tmp_path):
    seats = merge.pick_composite_seats(_l2(_MENU), 3)
    assert [s["code"] for s in seats] == ["000001", "000002", "000006"]
    assert seats[0]["name"] == "甲" and seats[0]["sector"] == "银行"


def test_seat_excludes_chase_st_and_pinned():
    """三条剔除各自单独可证 —— 少一条就会有一只本不该占席的票混进 BUY 候选池。"""
    seats = {s["code"] for s in merge.pick_composite_seats(_l2(_MENU), 6)}
    assert "000003" not in seats        # 当日 +10.0% ≥ CHASE_1D_PCT:追高在隔夜尺四年逐年为负
    assert "000004" not in seats        # *ST
    assert "000005" not in seats        # 📌 保送走自己的直通车


def test_seat_falls_back_to_composite_column():
    df = _l2([{"code": "000009", "name": "庚", "industry": "X", "composite": 80.0}])
    assert [s["code"] for s in merge.pick_composite_seats(df, 1)] == ["000009"]


def test_seat_is_empty_without_score_column():
    """两列都缺 → 空(presence-gated parity),不是拿别的列凑一个排序出来。"""
    df = _l2([{"code": "000009", "name": "庚", "industry": "X"}])
    assert merge.pick_composite_seats(df, 3) == []
    assert merge.pick_composite_seats(None, 3) == []
    assert merge.pick_composite_seats(_l2(_MENU), 0) == []


def test_seat_ordering_is_deterministic_on_ties():
    """同分按代码字典序 —— 席位进 BUY 候选池,不确定的排序等于不确定的 BUY。"""
    df = _l2([{"code": c, "name": c, "industry": "X", "gbdt_score": 70.0}
              for c in ("000009", "000007", "000008")])
    assert [s["code"] for s in merge.pick_composite_seats(df, 2)] == ["000007", "000008"]


# ── 注入 ────────────────────────────────────────────────────────────────────

def _judged(codes_convictions):
    return pd.DataFrame([
        {"code": c, "name": f"名{c}", "sector": "行业", "conviction": v,
         "thesis": f"论点{c}", "mechanism": f"机制{c}", "risk": "风险",
         "catalyst": "催化", "lane": "growth", "finalist": False}
        for c, v in codes_convictions])


def test_injected_seat_carries_l3_judgement(tmp_path):
    """**pinned 当年那次事故的同款**:只查 L2 会把 L3 的 thesis/mechanism 整段丢掉,
    下游 L4 prompt 就会说「本票无 L3 前提清单」,卡只好自己从 L1 重建前提。"""
    fin = pd.DataFrame([{"code": "000099", "name": "在场", "lane": "trend", "guard": ""}])
    out = merge.inject_composite_seats(
        fin, [{"code": "000002", "name": "乙", "sector": "半导体", "score": 70.5}],
        judged=_judged([("000002", 52)]))
    row = out[out["code"] == "000002"].iloc[0]
    assert row["guard"] == "composite_seat" and row["lane"] == "composite"
    assert row["thesis"] == "论点000002" and row["mechanism"] == "机制000002"
    assert row["conviction"] == 52


def test_injected_seat_without_judgement_does_not_fabricate(tmp_path):
    """L3 没判过它 → name/sector 用 L2 真值,conviction **留空不编**。"""
    out = merge.inject_composite_seats(
        pd.DataFrame([{"code": "000099", "name": "在场", "lane": "trend", "guard": ""}]),
        [{"code": "000001", "name": "甲", "sector": "银行", "score": 71.2}], judged=None)
    row = out[out["code"] == "000001"].iloc[0]
    assert row["name"] == "甲" and row["sector"] == "银行"
    assert pd.isna(row.get("conviction")) or row.get("conviction") in ("", None)


def test_seat_already_in_finalists_is_not_duplicated():
    """L3 自己也选了它 → 不重复行,只留痕;它的 L3 判断原样保留。"""
    fin = pd.DataFrame([{"code": "000001", "name": "甲", "lane": "trend",
                         "guard": "", "conviction": 68, "thesis": "L3 的论点"}])
    out = merge.inject_composite_seats(fin, [{"code": "000001", "name": "甲",
                                              "sector": "银行", "score": 71.2}])
    assert len(out) == 1
    assert out.iloc[0]["guard"] == "composite_seat"
    assert out.iloc[0]["thesis"] == "L3 的论点" and out.iloc[0]["lane"] == "trend"


def test_seat_does_not_overwrite_a_more_specific_guard():
    """`chase_backfill`/`lowturn_quota` 说的是「它怎么进来的」,席位说的是「它另外占了一席」。
    覆盖掉前者会把守卫链的账搞乱。"""
    fin = pd.DataFrame([{"code": "000001", "name": "甲", "lane": "lowturn",
                         "guard": "lowturn_quota"}])
    out = merge.inject_composite_seats(fin, [{"code": "000001", "name": "甲",
                                              "sector": "银行", "score": 71.2}])
    assert out.iloc[0]["guard"] == "lowturn_quota"


def test_empty_seats_is_byte_parity():
    fin = pd.DataFrame([{"code": "000099", "name": "在场", "lane": "trend", "guard": ""}])
    assert merge.inject_composite_seats(fin, []).equals(fin)


# ── 配置 / 回滚杆 ───────────────────────────────────────────────────────────

def test_seat_cfg_defaults_and_rollback():
    assert merge.composite_seat_cfg({"l3": {}}) == (True, merge.COMPOSITE_SEAT_M)
    assert merge.composite_seat_cfg({"l3": {"composite_seat": {"enabled": False}}})[0] is False
    assert merge.composite_seat_cfg({"l3": {"composite_seat": {"m": 5}}}) == (True, 5)
    # 坏值不静默变成一个奇怪的 M
    assert merge.composite_seat_cfg({"l3": {"composite_seat": {"m": "x"}}})[1] == merge.COMPOSITE_SEAT_M


# ── pass1 强留 ──────────────────────────────────────────────────────────────

def test_pass1_keeps_seats_so_l3_actually_judges_them():
    """席位若被 pass1 切掉,l3-rank 就从没判过它们 —— finalists 里那几行只能是空 thesis。"""
    rows = [dict(r, recall_channels="value", n_channels=1) for r in _MENU]
    # target=1:不强留的话,轮询只会留下 1 只
    kept, cut = triage.triage_l2_for_l3(_l2(rows), target=1, composite_seat_m=2)
    assert {"000001", "000002"} <= set(kept["code"])
    assert set(kept[kept["code"].isin(["000001", "000002"])]["selection_detail"]) == {"composite_seat"}


def test_pass1_seat_zero_is_parity():
    rows = [dict(r, recall_channels="value", n_channels=1) for r in _MENU]
    a, _ = triage.triage_l2_for_l3(_l2(rows), target=2, composite_seat_m=0)
    b, _ = triage.triage_l2_for_l3(_l2(rows), target=2)
    assert list(a["code"]) == list(b["code"])


# ── 端到端:write_finalists ─────────────────────────────────────────────────

def _scan(tmp_path, judged_rows):
    d = tmp_path / "2026-08-25"
    d.mkdir(parents=True)
    pd.DataFrame(_MENU).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps(judged_rows, ensure_ascii=False),
                                       encoding="utf-8")
    return d


def test_write_finalists_injects_seats_end_to_end(tmp_path):
    judged = [{"code": "000009", "name": "L3选的", "sector": "医药", "conviction": 60,
               "finalist": True, "thesis": "T", "mechanism": "M", "risk": "R",
               "catalyst": "C", "lane": "reversion", "triage_lean": "Hold"}]
    _scan(tmp_path, judged)
    res = merge.write_finalists("2026-08-25", budget=10, root=tmp_path,
                                pinned_path=tmp_path / "no-pinned.jsonc")
    assert res["composite_seat_n"] == 3
    assert res["composite_seats"] == ["000001", "000002", "000006"]
    fin = pd.read_csv(tmp_path / "2026-08-25" / "finalists.csv", dtype={"code": str})
    assert set(fin[fin["guard"] == "composite_seat"]["code"]) == {"000001", "000002", "000006"}
    assert "000009" in set(fin["code"])          # L3 自己选的那只照旧在场


def test_write_finalists_seat_disabled_is_parity(tmp_path, monkeypatch):
    """回滚杆:`l3.composite_seat.enabled=false` → 一只席位都不注入(逐字 parity)。"""
    monkeypatch.setattr(merge, "composite_seat_cfg", lambda cfg=None: (False, 0))
    judged = [{"code": "000009", "name": "L3选的", "sector": "医药", "conviction": 60,
               "finalist": True, "thesis": "T", "mechanism": "M", "risk": "R",
               "catalyst": "C", "lane": "reversion", "triage_lean": "Hold"}]
    _scan(tmp_path, judged)
    res = merge.write_finalists("2026-08-25", budget=10, root=tmp_path,
                                pinned_path=tmp_path / "no-pinned.jsonc")
    assert res["composite_seat_n"] == 0
    fin = pd.read_csv(tmp_path / "2026-08-25" / "finalists.csv", dtype={"code": str})
    assert list(fin["code"]) == ["000009"]


def test_seat_is_removed_from_bench(tmp_path):
    """同票不得既在 finalists 又在 bench —— pinned 那次的 M-1 同款(双记会让账本重复计入)。"""
    judged = [{"code": "000002", "name": "乙", "sector": "半导体", "conviction": 52,
               "finalist": False, "thesis": "T", "mechanism": "M", "risk": "R",
               "catalyst": "C", "lane": "growth", "triage_lean": "Hold"}]
    _scan(tmp_path, judged)
    merge.write_finalists("2026-08-25", budget=10, root=tmp_path,
                          pinned_path=tmp_path / "no-pinned.jsonc")
    bench = pd.read_csv(tmp_path / "2026-08-25" / "_l3_bench.csv", dtype={"code": str})
    fin = pd.read_csv(tmp_path / "2026-08-25" / "finalists.csv", dtype={"code": str})
    assert "000002" in set(fin["code"]) and "000002" not in set(bench.get("code", []))


def test_seats_may_exceed_pass1_target_and_that_is_intentional():
    """受保护集(📌 ∪ 席位)大于 target 时,kept 合法地略超 —— 强留优先级高于 target 配额
    (与 pinned 同一条既定纪律)。生产里 target=40、mandatory≈18,这条永不咬人;
    但把它钉住,免得后人看到「kept 43 > target 40」当故障去修。"""
    rows = [dict(r, recall_channels="value", n_channels=1) for r in _MENU]
    kept, _ = triage.triage_l2_for_l3(_l2(rows), target=1, composite_seat_m=3)
    assert len(kept) == 4                      # 1 只 pinned + 3 只席位
    assert {"000001", "000002", "000006", "000005"} == set(kept["code"])


def test_production_target_absorbs_seats_without_growing():
    """target 够大时 kept 恰为 target(席位在其中,不额外撑大)—— 这才是生产形状。"""
    rows = [dict(r, recall_channels="value", n_channels=1) for r in _MENU]
    kept, _ = triage.triage_l2_for_l3(_l2(rows), target=5, composite_seat_m=3)
    assert len(kept) == 5 and {"000001", "000002", "000006"} <= set(kept["code"])


def test_seat_written_by_l3_is_read_back_by_e6(tmp_path):
    """**接线锁**:守卫⑨ 写进 `finalists.csv` 的标记,必须正好是 E6 `_composite_seat_codes`
    读的那一个。两处各写一套 = 席位天天有、BUY 天天 BLOCKED,而且两边单测都绿
    (FN-1 家族:生产者没接线 / 消费者读没人生产的产物)。"""
    from autoresearch.scan.relative_buy import _composite_seat_codes

    judged = [{"code": "000009", "name": "L3选的", "sector": "医药", "conviction": 60,
               "finalist": True, "thesis": "T", "mechanism": "M", "risk": "R",
               "catalyst": "C", "lane": "reversion", "triage_lean": "Hold"}]
    d = _scan(tmp_path, judged)
    res = merge.write_finalists("2026-08-25", budget=10, root=tmp_path,
                                pinned_path=tmp_path / "no-pinned.jsonc")
    assert _composite_seat_codes(d) == set(res["composite_seats"]) != set()


def test_e6_seat_reader_accepts_either_marker(tmp_path):
    """`guard` 与 `lane` 是同批写下的两个标记,认哪个都行 —— 认两个更抗一侧被改。"""
    from autoresearch.scan.relative_buy import _composite_seat_codes

    d = tmp_path / "d"
    d.mkdir()
    (d / "finalists.csv").write_text(
        "code,guard,lane\n000001,composite_seat,\n000002,,composite\n000003,cap,trend\n",
        encoding="utf-8")
    assert _composite_seat_codes(d) == {"000001", "000002"}


def test_e6_seat_reader_is_empty_without_file(tmp_path):
    """文件缺席 → 空集 → 当天诚实 BLOCKED,不悄悄退回全体池。"""
    from autoresearch.scan.relative_buy import _composite_seat_codes

    assert _composite_seat_codes(tmp_path / "nope") == set()
