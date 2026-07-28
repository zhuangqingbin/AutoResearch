"""L4 prompt cache 前缀契约:共享块 byte-identical 且统一置于每张 prompt 头部区(spec T8)。
守的是 30 卡并发的 prompt cache 命中前提——当日件/简报若插进共享块之前或中段,30 卡前缀
全断、cache 全 miss。本测试冻结现状;若它红了 = 真实前缀断裂,按 bug 处理勿放宽断言。"""
from pathlib import Path

import json
import pandas as pd

from autoresearch.scan.agents.l4_card import write_dispatch_pack

SHARED = "# 当日共享指令\n地形X · 校准Y\n"


def _fixture(tmp_path: Path) -> Path:
    sd = tmp_path / "2026-07-08"
    sd.mkdir()
    (sd / "_l4_shared_instructions.md").write_text(SHARED, encoding="utf-8")
    pd.DataFrame({"code": ["000001", "600519"],
                  "ticker": ["000001.SZ", "600519.SS"]}).to_csv(sd / "finalists.csv", index=False)
    return sd


def test_prompts_share_byte_identical_head(tmp_path):
    sd = _fixture(tmp_path)
    write_dispatch_pack(sd)
    prompts = sorted(sd.glob("_l4_prompt_*.md"))
    assert len(prompts) == 2, "finalists 2 只应产 2 张 prompt"
    texts = [p.read_text(encoding="utf-8") for p in prompts]
    idx = [t.find(SHARED) for t in texts]
    assert all(i != -1 for i in idx), "共享块未原文进入 prompt"
    assert idx[0] == idx[1] and idx[0] <= 300, f"共享块位置不统一/不在头部区: {idx}"
    head_end = idx[0] + len(SHARED)
    assert texts[0][:head_end] == texts[1][:head_end], "头部前缀不 byte-identical(cache 必 miss)"


def test_legacy_mode_default_and_explicit_false_are_byte_identical(tmp_path):
    sd = _fixture(tmp_path)
    write_dispatch_pack(sd)
    default_bytes = {
        p.name: p.read_bytes() for p in sorted(sd.glob("_l4_prompt_*.md"))
    }
    for p in sd.glob("_l4_prompt_*.md"):
        p.unlink()

    write_dispatch_pack(sd, stable_context=False)

    assert {
        p.name: p.read_bytes() for p in sorted(sd.glob("_l4_prompt_*.md"))
    } == default_bytes


def test_stable_mode_puts_common_market_before_stock_specific_bytes(tmp_path):
    sd = _fixture(tmp_path)
    pd.DataFrame([
        {"code": "000001", "name": "平安银行", "sector": "银行", "industry": "银行"},
        {"code": "600519", "name": "贵州茅台", "sector": "食品饮料", "industry": "食品饮料"},
    ]).to_csv(sd / "finalists.csv", index=False)
    pd.DataFrame([
        {"code": "000001", "name": "平安银行", "industry": "银行", "pct_60d": 8.0,
         "above_ma60": 1.0, "pe": 6.0, "main_net_ratio": 0.01, "cmf_20": 0.2},
        {"code": "600519", "name": "贵州茅台", "industry": "食品饮料", "pct_60d": 2.0,
         "above_ma60": 1.0, "pe": 20.0, "main_net_ratio": 0.02, "cmf_20": 0.1},
    ]).to_csv(sd / "L1_scored_full.csv", index=False)
    pd.DataFrame([
        {"industry": "银行", "median_pct_60d": 8.0, "median_composite": 60, "n_recall": 1},
        {"industry": "食品饮料", "median_pct_60d": 2.0, "median_composite": 50, "n_recall": 1},
    ]).to_csv(sd / "sectors.csv", index=False)

    write_dispatch_pack(sd, stable_context=True)

    texts = [
        p.read_text(encoding="utf-8") for p in sorted(sd.glob("_l4_prompt_*.md"))
    ]
    assert all(t.index("## 市场地形") < t.index("## L4 派发 —") for t in texts)
    assert texts[0].split("## L4 派发 —", 1)[0] == texts[1].split(
        "## L4 派发 —", 1
    )[0]
    manifest = json.loads(
        (sd / "_l4_prompt_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["mode"] == "stable_context"
    assert set(manifest["prompts"]) == {"000001", "600519"}
    assert manifest["prompts"]["000001"]["blocks"]["market"]["content_sha256"]
