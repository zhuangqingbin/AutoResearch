"""影子买单记账:每日 top-k Hold(conviction 序)入 csv,幂等;回填历史。合成,无网络。

spec: 2026-07-05 wave §WS-A2 —— "如果门不拦,系统最想买的 3 只";评级基率/NAV 影子线的米仓。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.shadow_buys import backfill, record

# 门名与 ✓/✗ 必须紧邻(gate_status 解析:门名后一字符即判;真实卡格式如「主力真在✗」)
_CARD = ("# 决策卡\n\n**Rubric建议**: 6 维净分 +1/6 ｜ OW三门 主力真在✗·业绩真兑现✓·"
         "估值不透支✓ → **建议 Hold**\n**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")


def _mk_day(root, date, codes=("000001", "000002", "000003", "000004")):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"N{c}", "conviction": 90 - i * 10}
                  for i, c in enumerate(codes)]).to_csv(d / "finalists.csv", index=False)
    for c in codes:
        (d / "details" / f"{c}.md").write_text(_CARD, encoding="utf-8")
    pd.DataFrame([{"code": c, "close": 10.0 + i} for i, c in enumerate(codes)]).to_csv(
        d / "L1_scored_full.csv", index=False)
    return d


def test_record_topk_and_idempotent(tmp_path):
    d = _mk_day(tmp_path / "scan", "2026-07-03")
    out = tmp_path / "shadow_buys.csv"
    assert record(d, path=out, k=3) == 3
    df = pd.read_csv(out, dtype={"code": str})
    assert list(df["code"]) == ["000001", "000002", "000003"]        # conviction 降序 top-3
    assert df.iloc[0]["close"] == 10.0 and "主力真在" in df.iloc[0]["binding"]
    assert record(d, path=out, k=3) == 0                             # 幂等
    assert len(pd.read_csv(out)) == 3


def test_backfill_walks_days(tmp_path):
    root = tmp_path / "scan"
    _mk_day(root, "2026-07-02")
    _mk_day(root, "2026-07-03")
    out = tmp_path / "shadow_buys.csv"
    assert backfill(scan_root=root, path=out) == 6
    assert len(pd.read_csv(out)) == 6


def test_backfill_fault_isolation(tmp_path):
    """一日 finalists.csv 损坏时,backfill 跳过该日但继续处理其他日。"""
    root = tmp_path / "scan"
    good_day = _mk_day(root, "2026-07-02")
    bad_day = _mk_day(root, "2026-07-03")

    # 破坏 bad_day 的 finalists.csv:改成目录
    (bad_day / "finalists.csv").unlink()
    (bad_day / "finalists.csv").mkdir()

    out = tmp_path / "shadow_buys.csv"
    # backfill 不应抛,应返回好日的 3 行
    assert backfill(scan_root=root, path=out) == 3

    df = pd.read_csv(out, dtype={"code": str})
    assert len(df) == 3                                           # 只有好日的 3 行
    assert set(df["date"].unique()) == {"2026-07-02"}             # 只有好日期
