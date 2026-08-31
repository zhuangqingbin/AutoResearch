"""Unit tests for autoresearch.analyze.assemble pure helpers (filename / slug / A股 detection)."""
import json
import sys

import pytest

from autoresearch.analyze import assemble
from autoresearch.common import workspace as ws

#: `main()` 的 11 个必需分段文件(DECISION_REL + SPINE/APPENDIX 里 opt=False 的项),
#: 单一事实源直接从 assemble 模块派生 —— 不在测试里另写一份清单(两处清单迟早对不齐)。
_REQUIRED_REL = [assemble.DECISION_REL] + [
    rel for _, items in (assemble.SPINE + assemble.APPENDIX) for _, rel, opt in items if not opt
]


def _populate_required_files(root, decision_text: str) -> None:
    """铺满 `main()` 需要的 11 个必需分段文件；`decision.md` 用调用方传入的内容，其余占位。"""
    for rel in _REQUIRED_REL:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(decision_text if rel == assemble.DECISION_REL else f"# {rel} 占位内容\n",
                     encoding="utf-8")


@pytest.mark.unit
def test_is_ashare_matches_six_digit_codes_only():
    for t in ("600519", "000001.SZ", "688981.SS", "830799.BJ"):
        assert assemble._is_ashare(t)
    for t in ("NVDA", "0700.HK", "BTC-USD", "AAPL", ""):
        assert not assemble._is_ashare(t)


@pytest.mark.unit
def test_safe_name_strips_unsafe_chars_and_star_st():
    assert assemble._safe_name("*ST中潜") == "ST中潜"
    assert assemble._safe_name("a/b:c?d") == "abcd"
    assert assemble._safe_name("  ") == "未命名"          # empty after strip -> fallback


@pytest.mark.unit
def test_slug_keeps_cjk_drops_punct_spaces_to_dash():
    assert assemble._slug("S1 · 执行摘要 (PM)") == "s1-执行摘要-pm"


@pytest.mark.unit
def test_resolve_filename_ashare_falls_back_to_code(tmp_path):
    # no --name, no context to mine -> 6-digit code
    assert assemble._resolve_filename("600519.SS", tmp_path, None) == "600519"
    # explicit name wins
    assert assemble._resolve_filename("600519.SS", tmp_path, "贵州茅台") == "贵州茅台"
    # non-A-share -> ticker passthrough (safe-named)
    assert assemble._resolve_filename("NVDA", tmp_path, None) == "NVDA"


# ── D1.6/D8.3:硬校验 + manifest v2 + `_ashare_name_from_context` 日期格式修复 ──────


@pytest.mark.unit
def test_ashare_name_from_context_matches_dash_date(tmp_path):
    """修前:`_ashare_name_from_context` 拿目录名紧凑日期 `YYYYMMDD` 直接拼 `.md`,
    但 `harvest.py` 落盘用的是横杠日期 `YYYY-MM-DD`(`trade_date = date.today().isoformat()`)
    ——两处格式没对齐,兜底恒 None(实测复现:`context_claude/analyze/300308.SZ_20260830`
    目录 vs 真实文件 `context_claude/300308.SZ_2026-08-30.md`)。

    目录嵌套按生产真实层级搭:`root` = `<ctx>/analyze/<TICKER>_<YYYYMMDD>`，harvest md
    落在 `<ctx>/`(`root.parent.parent`)——与 `_output_dir(..., slim=False)` 用
    `ws.context_root()` 的真实产物位置一致。正文里名字与代码零间隔相邻
    (`中际旭创300308…`)才是抽取正则要求的真实新闻标题写法(纯 `\\n` 分隔不触发）。
    """
    ctx_root = tmp_path / "context_claude"
    root = ctx_root / "analyze" / "300308.SZ_20260830"
    root.mkdir(parents=True)
    (ctx_root / "300308.SZ_2026-08-30.md").write_text(
        "# Data context — 300308.SZ\n中际旭创300308今日发布公告。\n", encoding="utf-8")
    assert assemble._ashare_name_from_context("300308.SZ", root) == "中际旭创"


@pytest.mark.unit
def test_ashare_name_from_context_returns_none_when_md_absent(tmp_path):
    """仍然是兜底:harvest md 真的不存在时(而不是格式没对齐)必须回 None,不得报错。"""
    ctx_root = tmp_path / "context_claude"
    root = ctx_root / "analyze" / "600519.SS_20260830"
    root.mkdir(parents=True)
    assert assemble._ashare_name_from_context("600519.SS", root) is None


def _sandboxed_root(tmp_path, monkeypatch, dirname: str):
    """`chdir` 进 `tmp_path` 并按生产真实层级返回 `<ctx_root>/analyze/<dirname>`
    (与 `ws.context_root()`/`ws.reports_root()` 共享同一个 `tmp_path` 沙箱)。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.context_root() / "analyze" / dirname
    root.mkdir(parents=True)
    return root


@pytest.mark.unit
def test_assemble_fails_without_final_proposal(tmp_path, monkeypatch, capsys):
    """decision.md 有 `**Rating**` 行但缺 `FINAL TRANSACTION PROPOSAL` 行 → `main()` 返回 1，
    且不得在失败前写出报告/manifest(硬校验必须先于发布)。"""
    root = _sandboxed_root(tmp_path, monkeypatch, "600519.SS_20260830")
    _populate_required_files(root, "**Rating**: Hold\n\n(本卡缺 FINAL TRANSACTION PROPOSAL 行)\n")
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    assert assemble.main() == 1
    out = capsys.readouterr().out
    assert "FINAL TRANSACTION PROPOSAL" in out
    assert not (tmp_path / ws.reports_root()).exists()


@pytest.mark.unit
def test_assemble_fails_without_rating_line(tmp_path, monkeypatch, capsys):
    """decision.md 有 FINAL TRANSACTION PROPOSAL 行但缺 `**Rating**` 行 → `main()` 返回 1。"""
    root = _sandboxed_root(tmp_path, monkeypatch, "600519.SS_20260830")
    _populate_required_files(root, "(本卡缺 Rating 行)\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    assert assemble.main() == 1
    out = capsys.readouterr().out
    assert "Rating" in out


@pytest.mark.unit
def test_assemble_writes_manifest_v2_fields(tmp_path, monkeypatch):
    """完整合法卡 → `main()` 返回 0，manifest.json 是 v2 形状(原 6 键 + 7 新键)。"""
    root = _sandboxed_root(tmp_path, monkeypatch, "600519.SS_20260830")
    _populate_required_files(
        root, "**Rating**: Overweight\n\nFINAL TRANSACTION PROPOSAL: **BUY**\n")
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    assert assemble.main() == 0

    analyze_root = tmp_path / ws.reports_root() / "analyze"
    out_dirs = list(analyze_root.iterdir())
    assert len(out_dirs) == 1
    manifest = json.loads((out_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    # 原 6 键不丢
    assert manifest["ticker"] == "600519.SS"
    assert manifest["market"] == "A股"
    assert manifest["analysis_date"] == "2026-08-30"
    assert "generated_at" in manifest and "hhmm" in manifest
    # v2 新增 7 键
    assert manifest["schema_version"] == 2
    assert manifest["engine"] == ws.ENGINE
    assert manifest["run_id"] is None
    assert manifest["context_file"] is None       # 本用例未铺 harvest md
    assert manifest["degradations"] == 0
    assert manifest["rating"] == "Overweight"
    assert manifest["proposal"] == "BUY"


@pytest.mark.unit
def test_assemble_manifest_context_file_recorded_when_present(tmp_path, monkeypatch):
    """harvest md 真实存在时,manifest 的 `context_file` 记相对路径(不是 null)。"""
    root = _sandboxed_root(tmp_path, monkeypatch, "600519.SS_20260830")
    _populate_required_files(
        root, "**Rating**: Hold\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
    (tmp_path / ws.context_root()).mkdir(parents=True, exist_ok=True)
    (tmp_path / ws.context_root() / "600519.SS_2026-08-30.md").write_text(
        "# Data context — 600519.SS\n贵州茅台\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["assemble.py", str(root)])
    assert assemble.main() == 0

    analyze_root = tmp_path / ws.reports_root() / "analyze"
    out_dir = next(analyze_root.iterdir())
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["context_file"] == str(ws.context_root() / "600519.SS_2026-08-30.md")
