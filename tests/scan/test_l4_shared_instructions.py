"""共享指令稿生产者:文件必须在场,且逐卡 prompt 真的读到它。

2026-08-21(用户裁定「整个 learning 层退役」)本文件**换了被测契约**:原来它锁的是
共享块里那两样内容 —— prelude 的 📐/🔁/🚪 当日校准锚(`buy_ledger`/`cross_calib` 派生)
与 T+1 快环校准块 —— 两者都是闭环回注腿,已随账本删除。留下来的契约只剩一条,但它是
真的:**文件恒在场、内容 byte 稳定、且消费侧确实把它拼进了每张卡的 prompt**
(生产者接线了消费者没接 = 本仓 FN-1 家族;共享块缺文件则 prompt 前缀断裂,破 cache 契约)。
"""
from __future__ import annotations

from autoresearch.scan.agents import l4_card


def test_writes_a_stable_header_even_with_nothing_to_say(tmp_path):
    """无内容也要落一份稳定标头 —— 文件在场 = 逐卡共享块 byte-identical。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    n = l4_card.write_shared_instructions(d)
    assert n > 0
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    assert "当日共享块" in text


def test_repeated_writes_are_byte_identical(tmp_path):
    """同一天重复落稿必须逐字节一致 —— 否则每次重派都会把全卡 prompt 前缀改掉。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    l4_card.write_shared_instructions(d)
    first = (d / "_l4_shared_instructions.md").read_bytes()
    l4_card.write_shared_instructions(d)
    assert (d / "_l4_shared_instructions.md").read_bytes() == first


def test_no_learning_derived_content_leaks_back_in(tmp_path):
    """负锚:闭环回注腿不得复辟 —— 共享块里出现 📐/🔁/🚪 任一锚即红。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    l4_card.write_shared_instructions(d)
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    for mark in ("📐", "🔁", "🚪", "校准锚", "T+1 校准"):
        assert mark not in text, f"共享块出现闭环回注内容 {mark}(learning 层已于 2026-08-21 退役)"


def test_prompts_pick_up_shared_block(tmp_path):
    """端到端:生产者写的内容必须出现在逐卡 prompt 里(FN-1 家族:生产者接线了消费者没接)。"""
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name,conviction,lane\n000651,格力电器,70,composite\n",
                                     encoding="utf-8")
    marker = "当日共享块(全卡一致;确定性生成,勿逐卡改写)"
    l4_card.write_shared_instructions(d)
    l4_card.write_dispatch_pack(d)
    prompt = (d / "_l4_prompt_000651.md").read_text(encoding="utf-8")
    assert marker in prompt
