"""C1 硬门(design 2026-08-10-token-regression-remediation-design.md):
prompts 缺失必须在派发前拒绝 —— init 拒初始化、preflight 拒认领。合成,零网络。"""
from __future__ import annotations

from autoresearch.scan import l4_tasks

DATE = "2026-08-07"


def _write_prompt(tmp_path, code, body="# 任务包\nx" * 10):
    scan = tmp_path / DATE
    scan.mkdir(parents=True, exist_ok=True)
    (scan / f"_l4_prompt_{code}.md").write_text(body, encoding="utf-8")


def _init(tmp_path, codes):
    return l4_tasks.initialize(
        DATE, list(codes), root=tmp_path, context_root=tmp_path / "ctx")


def test_init_refuses_when_any_prompt_missing(tmp_path):
    _write_prompt(tmp_path, "600000")           # 600001 故意不写
    r = _init(tmp_path, ["600000", "600001"])
    assert r["ok"] is False
    assert r["missing_prompts"] == ["600001"]
    assert r["dispatch_batches"] == []
    # 硬门语义:拒绝时不得落盘任务簿(带病账本比没有账本更坏)
    assert not (tmp_path / DATE / "_l4_tasks.json").exists()


def test_init_refuses_on_empty_prompt_file(tmp_path):
    _write_prompt(tmp_path, "600000", body="")   # 0 字节 = 等同缺失
    r = _init(tmp_path, ["600000"])
    assert r["ok"] is False and r["missing_prompts"] == ["600000"]


def test_init_ok_when_all_prompts_present(tmp_path):
    for c in ("600000", "600001"):
        _write_prompt(tmp_path, c)
    r = _init(tmp_path, ["600000", "600001"])
    assert r["ok"] is True and r["n"] == 2
    assert (tmp_path / DATE / "_l4_tasks.json").exists()
