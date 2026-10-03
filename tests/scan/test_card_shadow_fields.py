"""L4 卡三条影子机读行(2026-10-03 B5):只记账,不进任何门。

复盘稿 §3.6:「兑现机制」裁决行的 ✓ 极性随前提表述翻转(40/41 个 ✓ 在确认「没有」机制),不可机读;
入场否决多数写在散文里;三档情景固定格式只解析出 78 张里的 10 张。三条新行把这三样变成可计分的事实。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.l4 import shadow_fields as sf

FULL = """# 决策卡 — 600519 贵州茅台 @ 2026-09-29
**三档情景(T+2 开盘口径)**: Bull 25% +4.5% / Base 45% −0.5% / Bear 30% −5.5% → **EV −0.75%**;**R:R 0.82**
[情景] bull 25% +4.5% · base 45% -0.5% · bear 30% -5.5%
**入场**: 条件(收盘站上 1.88)
**兑现机制**: 成立
[入场否决] close < 1.88 → 突破失败放弃
[入场否决] close >= 2.10 → 追高放弃
FINAL TRANSACTION PROPOSAL: **HOLD**
"""


def test_the_three_shadow_lines_parse_into_facts():
    got = sf.parse(FULL)
    assert got["mechanism"] == "成立"
    assert got["entry_vetoes"] == [{"op": "<", "threshold": 1.88, "raw": "close < 1.88 → 突破失败放弃"},
                                   {"op": ">=", "threshold": 2.1, "raw": "close >= 2.10 → 追高放弃"}]
    assert got["scenarios"] == {"bull": {"p": 25.0, "ret": 4.5}, "base": {"p": 45.0, "ret": -0.5},
                                "bear": {"p": 30.0, "ret": -5.5}}
    assert got["ev"] == pytest.approx(0.25 * 4.5 + 0.45 * -0.5 + 0.30 * -5.5)
    assert got["errors"] == []


def test_bulleted_lines_parse_the_same_as_bare_ones():
    """复审 I-3:卡里的 DSL 行全是 `- ` 开头(134/134 条 [执行线]);只认行首会把对的行记成错。"""
    bulleted = "\n".join(
        f"{lead}{line}" for lead, line in zip(("- ", "* ", "  - ", "1. ", "- "), (
            "**兑现机制**: 不成立",
            "[入场否决] close < 1.88 → 突破失败放弃",
            "[入场否决] close >= 2.10 → 追高放弃",
            "[情景] bull 25% +4.5% · base 45% -0.5% · bear 30% -5.5%",
            "[入场否决] close > 3 → 测试",
        ), strict=True))
    got = sf.parse(bulleted)
    assert got["errors"] == []
    assert got["mechanism"] == "不成立"
    assert [v["threshold"] for v in got["entry_vetoes"]] == [1.88, 2.1, 3.0]
    assert got["scenarios"]["base"] == {"p": 45.0, "ret": -0.5}


def test_missing_lines_are_unrecorded_not_negative():
    got = sf.parse("# 决策卡\n**入场**: 禁止\n")
    assert got["mechanism"] is None and got["scenarios"] is None and got["entry_vetoes"] == []


def test_a_mechanism_outside_the_three_states_is_an_error_not_a_guess():
    got = sf.parse("**兑现机制**: ✓ 机制存在\n")
    assert got["mechanism"] is None
    assert got["errors"] == ["兑现机制 不在 成立|不成立|未核 里:'✓ 机制存在'"]


def test_scenario_probabilities_must_sum_to_one_hundred():
    got = sf.parse("[情景] bull 30% +4% · base 30% 0% · bear 30% -4%\n")
    assert got["scenarios"] is None
    assert any("概率合计" in e for e in got["errors"])


def test_the_run_record_collects_every_card(tmp_path):
    (tmp_path / "details").mkdir()
    (tmp_path / "details" / "600519.md").write_text(FULL, encoding="utf-8")
    (tmp_path / "details" / "000001.md").write_text("**兑现机制**: 不成立\n", encoding="utf-8")

    path = sf.write(tmp_path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "_card_shadow_fields.json"
    assert doc["schema_version"] == 1 and doc["n_cards"] == 2
    assert doc["cards"]["000001"]["mechanism"] == "不成立"
    assert doc["coverage"] == {"mechanism": 2, "scenarios": 1, "entry_vetoes": 1}


def test_the_task_package_asks_for_the_lines_and_its_examples_parse_back():
    """A10 同款往返:派发给 l4-card 的说明里的样例,必须原样过本解析器 —— 说明与解析器同源。

    这三行走任务包「本次参数」块(`l4.shadow_fields`,缺省开),不进 agent 定义:定义已在字节预算线上,
    而且影子字段要能一键关掉。
    """
    from autoresearch.scan.l4.prompts import params_block

    block = params_block()
    assert "**兑现机制**: 成立|不成立|未核" in block
    examples = sf.EXAMPLES
    assert all(example in block for example in examples)
    assert sf.parse("\n".join(examples))["errors"] == []


def test_the_shadow_block_can_be_switched_off(monkeypatch):
    from autoresearch.scan import user_config
    from autoresearch.scan.l4.prompts import params_block

    real = user_config.knob
    monkeypatch.setattr(user_config, "knob", lambda b, k, e=None, d=None, cfg=None:
                        False if (b, k) == ("l4", "shadow_fields") else real(b, k, e, d, cfg))
    assert "兑现机制" not in params_block()
