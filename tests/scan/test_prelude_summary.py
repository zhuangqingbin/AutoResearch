"""prelude 汇总屏双写:12 步 ✓/✗ 屏必须完整落盘(scan-market.js 末15行截断的解药)。"""
from __future__ import annotations

from autoresearch.scan import prelude


def _results():
    return [{"step": "universe", "ok": True, "note": "L0 4100 → L1 1000 → L2 200"},
            {"step": "menu", "ok": False, "note": "RuntimeError: staging 缺"}]


def test_render_summary_has_every_step_with_mark():
    out = prelude.render_summary("2026-07-25", _results())
    assert "✓ universe" in out
    assert "✗ menu" in out
    assert "L0 4100 → L1 1000 → L2 200" in out
    assert "prelude 汇总" in out


def test_render_summary_includes_prewarm_state(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "预热" in out
    assert "✗" in out                      # 无 _prewarm.json → 明说没跑


def test_prewarm_line_detects_artifact(tmp_path):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    assert "✗" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)
    (d / "_prewarm.json").write_text('{"started_at": 1, "ended_at": 2}', encoding="utf-8")
    assert "✓" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)


def test_write_summary_file(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    p = prelude.write_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "✓ universe" in text and "✗ menu" in text
