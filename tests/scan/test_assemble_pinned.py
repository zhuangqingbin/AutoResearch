"""assemble 「📌 保送持仓」节 + buy-list 分列(feedback fb_20260714_001)。

用户裁定:保送(pinned)不占 L3 名额;L5 里保送持仓与真实精选**分列**——
- §3 投资建议(buy-list)只含真实精选(`lane!=pinned`),计数不含保送;
- 「## 📌 保送持仓」= 运行期 `lane==pinned` 行(`finalists.csv` 烤入的那次跑的事实)渲染成
  与 buy-list 同结构的完整表 + 「保送理由」列;expired 条目仍从 config 取尾注。

**run-time-truth 而非当前 config**:`pinned.jsonc` 在跑完后被改也不影响这份报告——finalists.csv
里的 `lane==pinned` 是那次运行的事实(旧设计把 presence-gate 挂在 `load_pinned()` 上,config 一改
旧保送票就从报告里凭空消失,而 §3 又已把它剔除 → 两头都没有;故改挂 lane)。config 只再供 expired。
"""
from __future__ import annotations

import json

from autoresearch.scan.assemble import build_summary


def _min_scan(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector,lane\n", encoding="utf-8")
    return d


def _scan_genuine_and_pinned(tmp_path):
    """1 只真实精选(600519·lane=value)+ 1 只保送(600000·lane=pinned),各一张卡。"""
    d = _min_scan(tmp_path)
    (d / "finalists.csv").write_text(
        "ticker,code,name,sector,lane,conviction,pinned_note\n"
        "600519,600519,真选票,食品饮料,value,72,\n"
        "600000,600000,持仓票,银行,pinned,55,长期观察仓\n", encoding="utf-8")
    dd = d / "details"
    dd.mkdir()
    (dd / "600519.md").write_text(
        "# 决策卡\n**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    (dd / "600000.md").write_text(
        "# 决策卡\n**Rating**: Underweight\nFINAL TRANSACTION PROPOSAL: **UNDERWEIGHT**\n", encoding="utf-8")
    return d


def _section(md: str, header: str) -> str:
    i = md.find(header)
    if i < 0:
        return ""
    j = md.find("\n## ", i + len(header))
    return md[i:j] if j != -1 else md[i:]


def test_pinned_excluded_from_buylist(tmp_path):
    """§3 buy-list 只含真实精选;保送票不进、计数不算保送。"""
    d = _scan_genuine_and_pinned(tmp_path)
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")
    s3 = _section(md, "## 3. 投资建议")
    assert "真选票" in s3
    assert "持仓票" not in s3
    assert "buy-list, 1 只" in s3


def test_pinned_shown_in_separate_table_by_runtime_lane(tmp_path):
    """保送票进「📌 保送持仓」完整表(卡评级 + 保送理由);靠 lane 而非当前 config。"""
    d = _scan_genuine_and_pinned(tmp_path)
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")   # 不传 pinned_path
    assert "## 📌 保送持仓" in md
    sec = _section(md, "## 📌 保送持仓")
    assert "持仓票" in sec and "长期观察仓" in sec and "Underweight" in sec
    assert "真选票" not in sec           # 真实精选不进保送表


def test_funnel_l3_count_excludes_pinned(tmp_path):
    """漏斗表 L3 出量 = 真实精选数(1),标注 (+1 保送直通)。"""
    d = _scan_genuine_and_pinned(tmp_path)
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")
    funnel = _section(md, "## 1. 漏斗")
    assert "| L3 | 精排 | 1" in funnel
    assert "保送" in funnel


def test_expired_footnote_still_from_config(tmp_path):
    """无 lane=pinned 行,但 config 有 expired 条目 → 节仍出现,列出已过期尾注。"""
    d = _min_scan(tmp_path)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "600001", "note": "老票该清了", "added": "2026-06-01", "expires": "2026-07-01"},
    ]), encoding="utf-8")
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200", pinned_path=pin)
    assert "## 📌 保送持仓" in md
    sec = _section(md, "## 📌 保送持仓")
    assert "已过期" in sec and "600001" in sec and "老票该清了" in sec


def test_missing_card_pinned_row_shows_placeholder(tmp_path):
    """保送票在 finalists(lane=pinned)但卡缺失 → 保送表里评级占位,不崩。"""
    d = _min_scan(tmp_path)
    (d / "finalists.csv").write_text(
        "ticker,code,name,sector,lane,pinned_note\n"
        "600002,600002,无卡持仓,券商,pinned,还没出卡\n", encoding="utf-8")
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")
    sec = _section(md, "## 📌 保送持仓")
    # buy-list/保送表按名称识别(代码列早已删),卡缺失时评级占位 + L4/目标列显示⚠️卡片缺失
    assert "无卡持仓" in sec and "卡片缺失" in sec and "还没出卡" in sec


def test_no_pinned_section_when_neither_runtime_nor_expired(tmp_path):
    """无 lane=pinned 行 + 无 config(默认路径经 conftest 隔离)→ 无节(老路不破)。"""
    md = build_summary(_min_scan(tmp_path), "2026-07-13", "1200", "20260713_1200")
    assert "📌 保送" not in md
    assert "## 1. 漏斗(数量)" in md
