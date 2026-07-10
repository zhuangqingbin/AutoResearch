#!/usr/bin/env python3
"""pinned.json —— 保送票 loader(cap/TTL)+ L1 强注 + L2 强留。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.1。
plan: docs/plans/2026-07-11-pinned-config-plan.md Task 3。

覆盖:①`load_pinned`(kept/expired 归类、cap 截断、expires 缺省推算、code 归一)②
`recall_select` 的 L1 强注(lane=pinned、不占 recall_n、不挤他票、湖缺数据占位)③
`select_l2` 的 L2 强留(不占 l2_n、不挤他票)。presence-gated parity:无 pinned → 两处
行为逐字节不变(`test_recall_select_composite_parity_*` / `test_select_l2_no_pinned_column_is_parity`)。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from autoresearch.common.scoring import _PRIOR_WEIGHTS, composite_score
from autoresearch.scan.recall.l2_stratify import select_l2
from autoresearch.scan.universe import recall_select
from autoresearch.scan.user_config import load_pinned
from tests.scan._synth_universe import synth_universe

DATE = "2026-07-11"


def _scored(n=50, seed=0) -> pd.DataFrame:
    """极简 composite-only 帧(够 composite 模式召回用;不含 channel 所需列)。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "composite": rng.uniform(0, 100, n),
    })


# ══════════════════════════ load_pinned ══════════════════════════


def test_load_pinned_missing_file_returns_empty(tmp_path):
    out = load_pinned(DATE, tmp_path / "nope.json")
    assert out == {"kept": [], "expired": []}


def test_load_pinned_empty_list_returns_empty(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text("[]", encoding="utf-8")
    assert load_pinned(DATE, p) == {"kept": [], "expired": []}


def test_load_pinned_valid_entry_kept_with_normalized_fields(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([
        {"code": "600519", "note": "长期仓", "added": "2026-07-01", "expires": "2026-07-20"},
    ]), encoding="utf-8")
    out = load_pinned(DATE, p)
    assert out["expired"] == []
    assert out["kept"] == [{"code": "600519", "note": "长期仓",
                            "added": "2026-07-01", "expires": "2026-07-20"}]


def test_load_pinned_code_suffix_and_short_form_normalized(tmp_path):
    """`.SH`/`.SS` 后缀 + 未 zfill 的短码 → 都归一成 6 位裸码(同项目 _code6 惯例)。"""
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([
        {"code": "600519.SH", "added": "2026-07-01", "expires": "2026-07-20"},
        {"code": "1", "added": "2026-07-01", "expires": "2026-07-20"},
    ]), encoding="utf-8")
    out = load_pinned(DATE, p)
    codes = {d["code"] for d in out["kept"]}
    assert codes == {"600519", "000001"}


def test_load_pinned_missing_expires_defaults_to_added_plus_ttl_trading_days(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([{"code": "600519", "added": "2026-07-01"}]), encoding="utf-8")
    out = load_pinned(DATE, p, ttl_days=10)
    got = date.fromisoformat(out["kept"][0]["expires"])
    # 独立 oracle(pandas bdate_range,不复用被测函数内部算法):added 后第 10 个工作日。
    want = pd.bdate_range(start=date(2026, 7, 1) + timedelta(days=1), periods=10)[-1].date()
    assert got == want


def test_load_pinned_missing_added_defaults_to_today(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([{"code": "600519"}]), encoding="utf-8")
    out = load_pinned(DATE, p, ttl_days=10)
    assert out["kept"][0]["added"] == DATE


def test_load_pinned_expired_entry_moves_to_expired_list(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([
        {"code": "600519", "note": "老票该清了", "added": "2026-06-01", "expires": "2026-07-01"},
    ]), encoding="utf-8")
    out = load_pinned(DATE, p)          # DATE=2026-07-11 > expires=2026-07-01
    assert out["kept"] == []
    assert out["expired"][0]["code"] == "600519"


def test_load_pinned_expires_equal_today_is_still_kept(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([{"code": "600519", "expires": DATE}]), encoding="utf-8")
    out = load_pinned(DATE, p)
    assert len(out["kept"]) == 1 and out["expired"] == []


def test_load_pinned_over_cap_truncates_in_file_order_and_warns(tmp_path, capsys):
    entries = [{"code": f"{i:06d}", "expires": "2026-12-31"} for i in range(7)]
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    out = load_pinned(DATE, p, cap=5)
    assert [d["code"] for d in out["kept"]] == [f"{i:06d}" for i in range(5)]
    assert out["expired"] == []                       # cap 溢出 ≠ 过期
    err = capsys.readouterr().err
    assert "[pinned]" in err and "cap" in err


def test_load_pinned_custom_cap_and_ttl_respected(tmp_path):
    entries = [{"code": f"{i:06d}", "expires": "2026-12-31"} for i in range(3)]
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    out = load_pinned(DATE, p, cap=2)
    assert len(out["kept"]) == 2


def test_load_pinned_missing_code_raises(tmp_path):
    p = tmp_path / "pinned.json"
    p.write_text(json.dumps([{"note": "缺 code"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="code"):
        load_pinned(DATE, p)


# ══════════════════════════ L1 强注(recall_select) ══════════════════════════


def test_recall_select_composite_parity_when_pinned_omitted_vs_none():
    scored = _scored()
    base = recall_select(scored, DATE, 20, "composite")
    explicit_none = recall_select(scored, DATE, 20, "composite", None, pinned=None)
    explicit_empty = recall_select(scored, DATE, 20, "composite", None, pinned=[])
    pd.testing.assert_frame_equal(base[0], explicit_none[0])
    pd.testing.assert_frame_equal(base[0], explicit_empty[0])
    assert "pinned" not in base[0].columns          # 无 pinned → 不新增任何列


def test_recall_select_composite_no_provenance_columns_leak_in_with_pinned():
    """composite 模式本就不带 recall_channels/n_channels/best_rank——pinned 激活也不应新增它们。"""
    scored = _scored()
    low_code = scored.sort_values("composite").iloc[0]["code"]     # 稳输给 top-20 composite
    recall, _ = recall_select(scored, DATE, 20, "composite", None,
                              pinned=[{"code": low_code, "note": "手工票"}])
    assert "recall_channels" not in recall.columns
    assert "n_channels" not in recall.columns


def test_recall_select_injects_new_ticker_beyond_recall_n():
    scored = _scored(n=50)
    recall_n = 20
    low_code = scored.sort_values("composite").iloc[0]["code"]    # 不在 top-20
    baseline, _ = recall_select(scored, DATE, recall_n, "composite")
    assert low_code not in set(baseline["code"])

    injected, _ = recall_select(scored, DATE, recall_n, "composite", None,
                                pinned=[{"code": low_code, "note": "手工票"}])
    assert len(injected) == recall_n + 1
    row = injected[injected["code"] == low_code].iloc[0]
    assert row["pinned"] and row["pinned_note"] == "手工票" and not row["data_missing"]


def test_recall_select_already_recalled_pinned_no_duplicate_row():
    scored = _scored(n=50)
    recall_n = 20
    top_code = scored.sort_values("composite", ascending=False).iloc[0]["code"]
    recall, _ = recall_select(scored, DATE, recall_n, "composite", None,
                              pinned=[{"code": top_code, "note": "本来就该进"}])
    assert len(recall) == recall_n                     # 没多一行
    assert (recall["code"] == top_code).sum() == 1      # 没变两行
    row = recall[recall["code"] == top_code].iloc[0]
    assert row["pinned"] and row["pinned_note"] == "本来就该进"


def test_recall_select_pinned_missing_from_lake_gets_placeholder():
    scored = _scored(n=50)
    ghost = "999999"
    assert ghost not in set(scored["code"])
    recall, _ = recall_select(scored, DATE, 20, "composite", None,
                              pinned=[{"code": ghost, "note": "湖里没有"}])
    row = recall[recall["code"] == ghost].iloc[0]
    assert row["data_missing"] and row["pinned"]
    assert pd.isna(row["composite"])                    # 不编数


def test_recall_select_does_not_squeeze_other_tickets():
    scored = _scored(n=50)
    recall_n = 20
    low_code = scored.sort_values("composite").iloc[0]["code"]
    baseline, _ = recall_select(scored, DATE, recall_n, "composite")
    injected, _ = recall_select(scored, DATE, recall_n, "composite", None,
                                pinned=[{"code": low_code}])
    non_pinned_after = set(injected.loc[~injected["pinned"], "code"])
    assert non_pinned_after == set(baseline["code"])    # 原 20 席一个不少一个不多


def test_recall_select_duplicate_pinned_entries_same_code_injected_once():
    scored = _scored(n=50)
    low_code = scored.sort_values("composite").iloc[0]["code"]
    recall, _ = recall_select(scored, DATE, 20, "composite", None,
                              pinned=[{"code": low_code, "note": "a"},
                                      {"code": low_code, "note": "b"}])
    assert (recall["code"] == low_code).sum() == 1


def test_recall_select_multi_mode_pinned_tagged_as_pinned_lane():
    uni = synth_universe(n=300, seed=11)
    scored = composite_score(uni, _PRIOR_WEIGHTS)
    recall_n = 60
    baseline, _ = recall_select(scored, DATE, recall_n, "multi")
    excluded = sorted(set(scored["code"]) - set(baseline["code"]))
    assert excluded, "universe 太小(全票被自然召回),测试前提不成立"
    low_code = excluded[0]

    recall, _ = recall_select(scored, DATE, recall_n, "multi", None,
                              pinned=[{"code": low_code, "note": "多路模式"}])
    row = recall[recall["code"] == low_code].iloc[0]
    assert row["recall_channels"] == "pinned"
    assert row["n_channels"] == 0
    assert row["pinned"]
    others = recall[recall["code"] != low_code]
    assert set(others["code"]) == set(baseline["code"])   # 原 60 席 provenance 不受影响


# ══════════════════════════ L2 强留(select_l2) ══════════════════════════


def _l2_universe(n=300, seed=5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    inds = rng.choice(["半导体", "白酒", "医药", "电力"], n)
    return pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "industry": inds,
        "composite": rng.uniform(0, 100, n),
        "recall_channels": "composite",
    })


def test_select_l2_no_pinned_column_is_parity():
    recall = _l2_universe()
    out, engine = select_l2(recall, 200)
    assert len(out) == 200
    assert "pinned" not in out.columns


def test_select_l2_pinned_column_all_false_is_parity():
    recall = _l2_universe()
    recall["pinned"] = False
    out, _ = select_l2(recall, 200)
    assert len(out) == 200


def test_select_l2_low_merit_pinned_retained_beyond_l2_n():
    recall = _l2_universe(n=300)
    recall["pinned"] = False
    low_idx = recall["composite"].idxmin()             # 稳输给分层采样
    low_code = recall.loc[low_idx, "code"]
    recall.loc[low_idx, "pinned"] = True

    baseline, _ = select_l2(recall[~recall["pinned"]].copy(), 200)
    assert low_code not in set(baseline["code"])

    out, _ = select_l2(recall, 200)
    assert len(out) == 201
    row = out[out["code"] == low_code].iloc[0]
    assert row["l2_lane_reserved"]


def test_select_l2_pinned_does_not_squeeze_other_tickets():
    recall = _l2_universe(n=300)
    recall["pinned"] = False
    low_idx = recall["composite"].idxmin()
    recall.loc[low_idx, "pinned"] = True

    rest_only, _ = select_l2(recall[~recall["pinned"]].copy(), 200)
    with_pinned, _ = select_l2(recall, 200)
    non_pinned_after = set(with_pinned.loc[~with_pinned["pinned"].fillna(False), "code"])
    assert non_pinned_after == set(rest_only["code"])


def test_select_l2_high_merit_pinned_no_duplicate_but_still_appended():
    """即便该票凭 merit 本就能挤进 200,pinned 仍不参与该竞争池 → 唯一一行 + 额外一席。"""
    recall = _l2_universe(n=300)
    recall["pinned"] = False
    top_idx = recall["composite"].idxmax()
    top_code = recall.loc[top_idx, "code"]
    recall.loc[top_idx, "pinned"] = True

    out, _ = select_l2(recall, 200)
    assert (out["code"] == top_code).sum() == 1
    assert len(out) == 201


def test_select_l2_pinned_rank_continues_past_l2_n():
    recall = _l2_universe(n=300)
    recall["pinned"] = False
    low_idx = recall["composite"].idxmin()
    recall.loc[low_idx, "pinned"] = True
    out, _ = select_l2(recall, 200)
    assert sorted(out["l2_rank"].tolist()) == list(range(1, 202))
