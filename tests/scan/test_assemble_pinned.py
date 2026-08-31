"""assemble 「📌 保送持仓」节 + 候选表分列(feedback fb_20260714_001)。

用户裁定:保送(pinned)不占 L3 名额;L5 里保送持仓与真实精选**分列**——
- 「## 候选(N 只)」(2026-08-28 前叫 `## 3. 投资建议`)只含真实精选(`lane!=pinned`),计数不含保送;
- 「## 📌 保送持仓」= 运行期 `lane==pinned` 行(`finalists.csv` 烤入的那次跑的事实)渲染成
  与候选表同结构的完整表 + 「保送理由」列;expired 条目仍从 config 取尾注。

**run-time-truth 而非当前 config**:`pinned.jsonc` 在跑完后被改也不影响这份报告——finalists.csv
里的 `lane==pinned` 是那次运行的事实(旧设计把 presence-gate 挂在 `load_pinned()` 上,config 一改
旧保送票就从报告里凭空消失,而候选表又已把它剔除 → 两头都没有;故改挂 lane)。config 只再供 expired。

2026-08-28 summary 精简重构(design 2026-08-28-summary-slimdown-design.md §4.1/§5):漏斗数量表
整体下沉 `appendix.md` B 节,所以原来「在 summary 里数 L3 出量」的断言跟着搬到 appendix 侧
(并加一条「不得留在 summary」的反向锁)。保送节本身仍在 summary(节 5)。
"""
from __future__ import annotations

import json

from autoresearch.scan.assemble import build_summary
from autoresearch.scan.report_appendix import render_appendix
from autoresearch.scan.report_sections import (
    attach_review,
    prepare_report_model,
    render_summary,
)


def _render(d, date="2026-07-13", hhmm="1200", folder="20260713_1200", pinned_path=None):
    """一次整形 → 两处纯渲染,返回 (summary, appendix)。

    顺序照 publisher 的冻结时序(prepare → render_summary → attach_review → render_appendix)——
    appendix A 节要读 `attach_review` 回填的全文 banner,少这一步 A 节会印 ABSENT。
    `build_summary()` 兼容壳仍在(见下方只需 summary 的用例),这里只是同一模型多渲一份。
    """
    model = prepare_report_model(d, date, hhmm, folder, pinned_path=pinned_path)
    summary = render_summary(model)
    return summary, render_appendix(attach_review(model, summary))


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
    """候选表只含真实精选;保送票不进、计数不算保送(只数现在写在节标题里:`## 候选(1 只)`)。"""
    d = _scan_genuine_and_pinned(tmp_path)
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")
    s3 = _section(md, "## 候选(")
    assert s3.strip(), "候选表节切片为空 —— 锚点失效,后面的断言会变成恒绿"
    assert "真选票" in s3
    assert "持仓票" not in s3
    assert s3.startswith("## 候选(1 只)"), f"候选只数应为 1(不含保送): {s3.splitlines()[0]}"


def test_pinned_shown_in_separate_table_by_runtime_lane(tmp_path):
    """保送票进「📌 保送持仓」完整表(卡评级 + 保送理由);靠 lane 而非当前 config。"""
    d = _scan_genuine_and_pinned(tmp_path)
    md = build_summary(d, "2026-07-13", "1200", "20260713_1200")   # 不传 pinned_path
    assert "## 📌 保送持仓" in md
    sec = _section(md, "## 📌 保送持仓")
    assert "持仓票" in sec and "长期观察仓" in sec and "Underweight" in sec
    assert "真选票" not in sec           # 真实精选不进保送表
    # §4.1 节 5:与候选表同列集 + 末尾「保送理由」列(仍分列,不并表)
    header = next(ln for ln in sec.splitlines() if ln.lstrip().startswith("| #"))
    assert all(c in header for c in ("评级", "目标(EV)", "一句依据", "L1→L2", "保送理由")), header
    assert "L3精排" not in header and "L1召回" not in header, f"保送表未跟着换新列集: {header}"


def test_funnel_l3_count_excludes_pinned(tmp_path):
    """漏斗表 L3 出量 = 真实精选数(1),标注 (+1 保送直通)。

    2026-08-28:漏斗数量表整体下沉 appendix B(§5 行 17),断言跟着搬 —— 数还是那个数,
    只是换了页。同时反向锁住它**不得**留在决策层(重复展开是本波要治的病)。
    """
    d = _scan_genuine_and_pinned(tmp_path)
    md, ap = _render(d)
    funnel = _section(ap, "## B. 漏斗现场")
    assert funnel.strip(), "appendix B 切片为空 —— 锚点失效,断言会变成恒绿"
    assert "| L3 | 精排 | 1" in funnel
    assert "保送" in funnel
    assert "| L3 | 精排 |" not in md, "漏斗数量表应只在 appendix B 展开"


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
    """无 lane=pinned 行 + 无 config(默认路径经 conftest 隔离)→ 无节(老路不破)。

    对照锚:保送节缺席时报告并没有整体渲染失败 —— 漏斗现场照常产出,只是它现在
    在 appendix B(旧断言锁的是 summary 里的 `## 1. 漏斗(数量)`)。
    """
    md, ap = _render(_min_scan(tmp_path))
    assert "📌 保送" not in md
    assert "📌 保送" not in ap
    assert "## B. 漏斗现场" in ap and "**漏斗数量**" in ap
    assert "## 1. 漏斗" not in md, "旧节号不得复辟"
