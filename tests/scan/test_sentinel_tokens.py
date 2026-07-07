"""哨兵建议 + 哨兵红队对象 + token 估算器补全。合成,无网络。

spec: docs/specs/2026-07-03-scan-sentinel-economy-design.md
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.menu import sentinel_advice


def _mk(tmp_path, frac_healthy, n=200, regime=None):
    d = tmp_path / "s"
    d.mkdir(exist_ok=True)
    k = int(n * frac_healthy)
    rows = [{"code": f"{i:06d}", "pct_60d": 15.0, "main_net_ratio": 0.05, "cmf_20": 0.1}
            for i in range(k)]
    rows += [{"code": f"{i:06d}", "pct_60d": -30.0, "main_net_ratio": -0.01, "cmf_20": -0.1}
             for i in range(k, n)]
    pd.DataFrame(rows).to_csv(d / "L1_scored_full.csv", index=False)
    if regime is not None:
        (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    return d


def test_sentinel_levels(tmp_path):
    assert sentinel_advice(_mk(tmp_path, 0.01))[0] == "sentinel"           # 材料枯竭
    assert sentinel_advice(_mk(tmp_path, 0.04, regime="risk_off"))[0] == "sentinel"
    assert sentinel_advice(_mk(tmp_path, 0.04, regime="range"))[0] == "consider"
    level, reason = sentinel_advice(_mk(tmp_path, 0.06, regime="range"))   # 07-02 口径 6.2% → full
    assert level == "full" and "材料充足" in reason


def test_sentinel_degrades(tmp_path):
    assert sentinel_advice(tmp_path)[0] == "full"                          # 无 L1 → 全扫
    d = tmp_path / "s2"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "pct_60d": 5.0}]).to_csv(d / "L1_scored_full.csv", index=False)
    level, reason = sentinel_advice(d)
    assert level == "full" and "降级" in reason                             # 谓词缺列


def test_pick_sentinel_candidates(tmp_path):
    from autoresearch.scan.agents.l4_card import pick_sentinel_candidates
    d = tmp_path / "s"
    d.mkdir()
    pd.DataFrame([{"code": "000001", "gbdt_score": 50.0}, {"code": "000002", "gbdt_score": 90.0},
                  {"code": "000003", "gbdt_score": 70.0}]).to_csv(d / "L2_gbdt_top200.csv", index=False)
    assert pick_sentinel_candidates(d, k=2) == ["000002", "000003"]
    assert pick_sentinel_candidates(tmp_path / "nope") == []


def test_token_estimate_rows(tmp_path):
    """估算器:策略师行/L3(csv+落稿)/L4 prompt 计数;缺稿 → —。"""
    from autoresearch.scan.assemble import _stage_token_estimate
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    (d / "market_view.md").write_text("研判" * 100, encoding="utf-8")
    (d / "_l3_table.md").write_text("表" * 500, encoding="utf-8")
    (d / "L3_judged_full.csv").write_text("code\n000001\n", encoding="utf-8")
    (d / "details" / "000001.md").write_text("卡" * 200, encoding="utf-8")
    (d / "_l4_prompt_000001.md").write_text("p" * 300, encoding="utf-8")
    md = "\n".join(_stage_token_estimate(d))
    assert "旁路 策略师 | Opus | session | — | 1" in md        # 07-06:引擎后加 effort+墙钟列(无计时→墙钟—)
    assert "落稿契约" in md and "未计而非为零" in md
    md2 = "\n".join(_stage_token_estimate(tmp_path))           # 空目录 → 全 —
    assert "旁路 策略师 | Opus | session | — | — | —" in md2


def test_token_estimate_l4_input_slim_row_and_prompt_hint(tmp_path):
    """输入侧纠偏:slim 落稿计入 L4 输入行;卡在而 prompt 稿缺 → 提示用 l4_card prompts 落稿。"""
    from autoresearch.scan.assemble import _stage_token_estimate
    d = tmp_path / "context" / "scan" / "2026-07-03"
    (d / "details").mkdir(parents=True)
    (d / "details" / "600584.md").write_text("卡" * 200, encoding="utf-8")
    (tmp_path / "context" / "600584.SS_2026-07-03_slim.md").write_text("数" * 1000, encoding="utf-8")
    md = "\n".join(_stage_token_estimate(d))
    assert "L4 输入·slim" in md
    assert "l4_card prompts" in md          # prompt 稿缺 → 可执行提示

    (d / "_l4_prompt_600584.md").write_text("p" * 300, encoding="utf-8")
    md2 = "\n".join(_stage_token_estimate(d))
    assert "l4_card prompts" not in md2     # 落稿后提示消失
