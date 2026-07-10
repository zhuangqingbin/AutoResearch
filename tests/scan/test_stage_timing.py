"""墙钟 mtime 推导契约:锚全在→7键齐;锚缺→键略过;负跨度略过;已有键优先且推导补缺写回。"""
import json
import os
import time
from pathlib import Path

from autoresearch.scan.stage_timing import derive_stage_timing, ensure_stage_timing


def _touch(p: Path, ts: float):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (ts, ts))


def _fixture(tmp_path: Path) -> Path:
    det = tmp_path / "context" / "scan" / "2026-07-10"
    t = time.time() - 10_000
    _touch(det / "_t0.json", t)
    _touch(det / "market_pack.json", t + 30)
    _touch(det / "market_view.md", t + 120)
    _touch(det / "L2_gbdt_top200.csv", t + 600)
    _touch(det / "sector_briefs" / "半导体.md", t + 900)
    _touch(det / "_l3_table.md", t + 960)
    _touch(det / "_l3_judged.json", t + 1800)
    _touch(det / "_l4_prompt_000001.md", t + 1900)
    _touch(det.parent.parent / "000001.SZ_2026-07-10_slim.md", t + 2200)
    _touch(det / "details" / "000001.md", t + 2500)
    return det


def test_derive_all_keys(tmp_path):
    tm = derive_stage_timing(_fixture(tmp_path))
    assert tm["L0L1L2"]["wall_s"] == 600          # _t0 → L2 csv
    assert tm["策略师"]["wall_s"] == 90            # pack → view
    assert tm["行业brief"]["wall_s"] == 300        # max(L2,view)=t+600 → brief
    assert tm["L3精排"]["wall_s"] == 840           # 表 → judged
    assert tm["L4slim"]["wall_s"] == 300           # prompts → slim
    assert tm["L4研究"]["wall_s"] == 600           # prompts → 卡
    assert tm["总计"]["wall_s"] == 2500            # _t0 → 最晚产物


def test_missing_anchor_skips_key(tmp_path):
    det = _fixture(tmp_path)
    (det / "_l3_judged.json").unlink()
    tm = derive_stage_timing(det)
    assert "L3精排" not in tm
    assert "L0L1L2" in tm                          # 其余键不连坐


def test_all_reused_cards_negative_span_skipped(tmp_path):
    det = _fixture(tmp_path)                        # 全复用卡:卡 mtime 早于 prompts → 负跨度
    old = (det / "_l4_prompt_000001.md").stat().st_mtime - 50
    os.utime(det / "details" / "000001.md", (old, old))
    assert "L4研究" not in derive_stage_timing(det)


def test_ensure_respects_existing_and_writes(tmp_path):
    det = _fixture(tmp_path)
    (det / "_stage_timing.json").write_text(json.dumps({"L3精排": {"wall_s": 7}}), encoding="utf-8")
    merged = ensure_stage_timing(det)
    assert merged["L3精排"]["wall_s"] == 7          # 编排/人工写的优先
    on_disk = json.loads((det / "_stage_timing.json").read_text(encoding="utf-8"))
    assert on_disk["L0L1L2"]["wall_s"] == 600       # 推导补缺已写回
