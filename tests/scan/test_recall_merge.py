"""quota_union:并集去重 / floor 保底 / 恰 recall_n / provenance / backfill / 确定性。NO network。"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.recall.merge import quota_union
from autoresearch.scan.recall.registry import ChannelSpec


def _base(n=300):
    return pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                         "composite": [n - i for i in range(n)], "name": [f"s{i}" for i in range(n)]})


def _cf(codes, scores):
    return pd.DataFrame({"code": [f"{c:06d}" for c in codes], "channel_rank": range(1, len(codes) + 1),
                         "channel_score": scores})


def test_union_dedup_and_provenance():
    base = _base()
    frames = {"a": _cf([0, 1, 2], [9, 8, 7]), "b": _cf([2, 3, 4], [9, 8, 7])}
    defs = {"a": ChannelSpec("a", 3, 1), "b": ChannelSpec("b", 3, 1)}
    merged, longf = quota_union(frames, defs, recall_n=5, base_frame=base)
    assert set(merged["code"]) == {"000000", "000001", "000002", "000003", "000004"}
    row2 = merged[merged["code"] == "000002"].iloc[0]
    assert row2["n_channels"] == 2 and row2["recall_channels"] == "a|b"
    assert len(longf) == 6   # 3 + 3 长表行


def test_floor_protects_each_channel_top():
    frames = {"a": _cf(list(range(10)), list(range(10, 0, -1))),
              "b": _cf([100, 101, 102, 103], [9, 8, 7, 6])}
    defs = {"a": ChannelSpec("a", 10, 2), "b": ChannelSpec("b", 4, 2)}
    merged, _ = quota_union(frames, defs, recall_n=4, base_frame=_base(200))
    # b 的 top-2(000100,000101)必须在(floor 保底),即便 composite 低
    assert {"000100", "000101"} <= set(merged["code"])
    assert len(merged) == 4


def test_exactly_recall_n_with_backfill():
    base = _base(300)
    frames = {"a": _cf([0, 1, 2], [9, 8, 7])}            # 并集仅 3
    defs = {"a": ChannelSpec("a", 3, 1)}
    merged, _ = quota_union(frames, defs, recall_n=10, base_frame=base)
    assert len(merged) == 10                              # backfill 到 10
    assert (merged["recall_channels"] == "(backfill)").sum() == 7


def test_deterministic():
    base = _base()
    frames = {"a": _cf([0, 1, 2], [9, 8, 7]), "b": _cf([2, 3, 4], [9, 8, 7])}
    defs = {"a": ChannelSpec("a", 3, 1), "b": ChannelSpec("b", 3, 1)}
    m1, _ = quota_union(frames, defs, 5, base)
    m2, _ = quota_union(frames, defs, 5, base)
    pd.testing.assert_frame_equal(m1, m2)
