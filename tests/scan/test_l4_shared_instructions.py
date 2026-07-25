"""共享指令稿生产者:此前全仓无生产者,当日校准行从未到达任何一张卡(Wave5 ④B)。"""
from __future__ import annotations

from autoresearch.scan.agents import l4_card


def test_writes_file_with_calib_lines(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 目标价校准:触达率 44%——目标幅>+4% 需超额理由"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "🔁 T+1 校准:近 10 日方向票 4 准 2 不准")
    n = l4_card.write_shared_instructions(d)
    assert n > 0
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    assert "目标价校准" in text
    assert "T+1 校准" in text


def test_banned_lines_are_filtered(tmp_path, monkeypatch):
    """含「禁注」的行是样本不足的自我标注,贴进 prompt = 用坏先验污染判断。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 好行:触达率 44%",
                                         "🚪 门柱:n=3 样本不足,禁注 skeptic 先验"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    l4_card.write_shared_instructions(d)
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    assert "好行" in text
    assert "禁注" not in text


def test_empty_sources_still_write_stable_header(tmp_path, monkeypatch):
    """无校准行也要落一份稳定标头:文件在场 = 逐卡共享块 byte-identical(缺文件才是 cache 断裂)。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    n = l4_card.write_shared_instructions(d)
    assert n > 0
    assert "当日共享块" in (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")


def test_prompts_pick_up_shared_block(tmp_path, monkeypatch):
    """端到端:生产者写的内容必须出现在逐卡 prompt 里(生产者接线了消费者没接=本仓 FN-1 家族)。"""
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name,conviction,lane\n000651,格力电器,70,composite\n",
                                     encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 独有标记 ZZZ9"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    l4_card.write_shared_instructions(d)
    l4_card.write_dispatch_pack(d)
    prompt = (d / "_l4_prompt_000651.md").read_text(encoding="utf-8")
    assert "ZZZ9" in prompt
