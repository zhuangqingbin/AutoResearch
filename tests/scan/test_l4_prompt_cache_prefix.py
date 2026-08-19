"""L4 prompt cache 前缀契约:共享块 byte-identical 且统一置于每张 prompt 头部区(spec T8)。
守的是 30 卡并发的 prompt cache 命中前提——当日件/简报若插进共享块之前或中段,30 卡前缀
全断、cache 全 miss。本测试冻结现状;若它红了 = 真实前缀断裂,按 bug 处理勿放宽断言。"""
from pathlib import Path

import pandas as pd

from autoresearch.scan.agents.l4_card import write_dispatch_pack
import json  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

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


# Wave10 B4:`stable_context` 参数退役,原两条用例随之删除 ——
#   · `test_legacy_mode_default_and_explicit_false_are_byte_identical`:参数没了,
#     「默认 == 显式 False」变成恒真的同义反复;
#   · `test_stable_mode_puts_common_market_before_stock_specific_bytes`:测的是已删分支。
# 它们顺带锁的「共享块必须在逐股字节之前 + 头部前缀 byte-identical」由上面那条
# `test_prompts_share_byte_identical_head` 完整覆盖(legacy 现在是唯一的路),无孤儿契约。
