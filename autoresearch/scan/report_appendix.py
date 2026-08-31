#!/usr/bin/env python3
"""`appendix.md` 现场附录的**纯渲染层**(零 LLM、零 IO)。

design: docs/specs/2026-08-28-summary-slimdown-design.md §4.2(骨架)/ §4.3(文风)/ §5(去向)/ §6.5 / §6.9 / §6.10

## 铁律

1. **纯函数**:`render_appendix(model)` 只读 `ReportModel`。不读盘、不写盘、不重算评级、
   不 import `pathlib`。素材缺席 → 印 `ABSENT`,**不靠删整节表达 absence**
   (否则「缺任一节」检查会把 sentinel / 无 Tier-3 的合法缺席误报成丢件)。
2. **不是第三份权威源**:appendix 是同一 `ReportModel` 的诊断展开视图。权威仍是
   `_final_ratings.json` / `decision_records.json` / `details/` / `trace/`;
   已完整存在于 `details/` 的 L4 卡与 `trace/` 的法证文件**只列索引链接,不复制全文**。
3. **文风(§4.3)**:数字先行、一行一事实、正文不出现「口径:」「注:」「由来:」这类夹注词
   —— 注文本来就集中在 F 节,由 `###` 标题承载。

## 节序(恒在,A→G)

    A 自检明细 / B 漏斗现场 / C 研究全文 / D 门柱与资格 / E 运行观测 / F 方法与口径 / G 诚实局限

标题与 `<a id>` 锚由 `report_model.APPENDIX_SECTIONS` / `APPENDIX_ANCHORS` 单一事实源驱动:
新增节只改那两张表,本模块的骨架循环自动跟上。
"""
from __future__ import annotations

from typing import Any

from autoresearch.common.ruler import MAIN_RULER
from autoresearch.scan.report_model import (
    ABSENT,
    APPENDIX_ANCHORS,
    APPENDIX_SECTIONS,
    APPENDIX_WARN_BYTES,
    FOOTNOTES,
    METHOD_ANCHORS,
    RUN_OBSERVATION_DETAIL_MARKERS,
    ReportModel,
)

#: 六段漏斗免责行(旧 summary H1 下那一行;§5 行 1 → 附录 G)。summary 只留身份行。
FUNNEL_DISCLAIMER = ("六段漏斗:选集→召回→粗排(分层采样)→精排→研究→整合。"
                     "L0/L1/L2 确定性,L3/L4 Claude 为引擎,**仅供研究,非投资建议。**")

#: 诚实局限三条全文(旧 summary 节 22;§5 行 22 → 附录 G,summary 只留一行 + 链接)。
#: **单一事实源**:summary 侧要印摘要行时从这里取,不再各写一份。
HONEST_LIMITATIONS: tuple[str, ...] = (
    f"召回/粗排为启发式 + {MAIN_RULER} 超短主尺 IC 校准(L1 复合分、L2 sn_composite 同口径;"
    "T+1/T+5 参考),随 regime 漂移;L3/L4 为 Claude 推理产出。",
    "业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
    "A股涨跌停/停牌使名义止损未必可执行(见各决策卡执行段)。",
)

_FENCE = "```"


# ── 文本原语(全部纯字符串,不碰文件系统)──────────────────────────────────
def _flat(v: Any) -> str:
    """把 CSV 里可能带换行/竖线的自由文本压成一行(不截断 —— C 节要的是全文)。"""
    s = "" if v is None else str(v)
    return " ".join(s.replace("|", "/").split())


def _normalize_headings(text: str) -> str:
    """嵌入块自带的 `#`~`###` 标题一律压成 `###`;`####` 及更深保留。

    附录的 `##` 是 A–G 七节骨架的独占层级。生产者块(如 `## 各阶段耗时 & 落盘字节`)
    若原样嵌进 E 节,会在节内劈出一个同级兄弟节,锚导航当场断掉。围栏代码块内不动
    (`# 注释` 不是标题)。
    """
    out: list[str] = []
    in_fence = False
    for ln in text.split("\n"):
        if ln.lstrip().startswith(_FENCE):
            in_fence = not in_fence
        elif not in_fence:
            s = ln.lstrip()
            if s.startswith("#"):
                depth = len(s) - len(s.lstrip("#"))
                if 1 <= depth <= 3 and s[depth : depth + 1] in (" ", "\t"):
                    ln = "### " + s[depth:].strip()
        out.append(ln)
    return "\n".join(out)


def _leads_with_heading(text: str) -> bool:
    for ln in text.split("\n"):
        if ln.strip():
            return ln.lstrip().startswith("#")
    return False


def _block(label: str, text: str, *, self_labeled: bool = False) -> list[str]:
    """一块素材:自带标题/自带标签 → 直接嵌;否则补一行粗体标签;缺席 → 一行 `ABSENT`。

    `self_labeled=True` 用于生产者块首行已经写了自己的名字(门柱直方图、数据降级行)——
    再套一层同名标签就是把同一事实说两遍。缺席时仍用 `label` 印 ABSENT,节结构不变。
    """
    body = (text or "").strip()
    if not body:
        return [f"**{label}**:{ABSENT}", ""]
    body = _normalize_headings(body)
    return ([body, ""] if self_labeled or _leads_with_heading(body)
            else [f"**{label}**", "", body, ""])


def _lines_block(label: str, lines: list[str] | tuple[str, ...]) -> list[str]:
    return _block(label, "\n".join(str(x) for x in (lines or [])))


# ── A. 自检明细 ─────────────────────────────────────────────────────────
def _review_failures(model: ReportModel) -> list[dict]:
    """`review_result.failures` 逐条(prepare 侧 `attach_review()` 回填;渲染层不重跑自检)。"""
    v = (model.review_result or {}).get("failures")
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _failure_lines(rows: list[dict]) -> list[str]:
    """banner 缺席时的兜底逐条明细(键名两套都认:`key`/`msg` 与旧 `check`/`detail`)。"""
    n_fail = sum(1 for x in rows if x.get("severity") == "fail")
    n_warn = sum(1 for x in rows if x.get("severity") == "warn")
    out = [f"fail {n_fail} / warn {n_warn}", ""]
    for x in rows:
        mark = "🛑" if x.get("severity") == "fail" else "⚠️"
        name = x.get("key") or x.get("check") or "—"
        msg = x.get("msg") or x.get("detail") or "—"
        out.append(f"- {mark} **{name}**:{_flat(msg)}")
    return out


def _section_self_review(model: ReportModel) -> list[str]:
    """自检**全文**(非聚合版;summary 顶那条是聚合版)。

    素材只有两个,都由 prepare 侧回填:`banner_full`(首选,逐条原样)与 `review_result`
    (兜底)。**渲染层不回头跑自检** —— 那条路要读 staging,附录是纯函数。
    两者都印会把同一批 fail/warn 说两遍,所以 banner 在场时不再展开明细表。
    """
    body = (model.banner_full or "").strip()
    if body:
        return [_normalize_headings(body), ""]
    rows = _review_failures(model)
    return (_failure_lines(rows) + [""]) if rows else [ABSENT, ""]


# ── B. 漏斗现场 ─────────────────────────────────────────────────────────
def _section_funnel(model: ReportModel) -> list[str]:
    return (_lines_block("漏斗数量", model.funnel_table_lines)
            + _block("数据降级", model.degraded_line, self_labeled=True)
            + _lines_block("各阶段卡点", model.stage_overview_lines)
            + _block("L2 菜单体检", model.menu_health_block)
            + _block("0 买机制", model.funnel_readout))


# ── C. 研究全文 ─────────────────────────────────────────────────────────
def _l3_lines(finals_rows: list[dict]) -> list[str]:
    """L3 逐票全文(论点/风险/催化/conviction)+ 指向 `details/` 的**索引链接**。

    L4 决策卡本身已完整落在 `details/<名称>.md`,这里只给链接,不复制卡面全文(§4.1.2)。
    """
    if not finals_rows:
        return []
    out = [f"**L3 逐票({len(finals_rows)} 只)**", ""]
    for fr in finals_rows:
        name = _flat(fr.get("name"))
        code = _flat(fr.get("code") or fr.get("ticker"))
        sector = _flat(fr.get("sector") or fr.get("industry"))
        conv = _flat(fr.get("conviction"))
        head = f"- **{name or '—'}({code or '—'})**"
        if sector:
            head += f" · {sector}"
        if conv:
            head += f" · conv {conv}"
        if name:
            head += f" · [决策卡](details/{name}.md)"
        out.append(head)
        for label, key in (("论点", "thesis"), ("风险", "risk"), ("催化", "catalyst")):
            out.append(f"  - {label}:{_flat(fr.get(key)) or '—'}")
    out.append("")
    return out


def _section_research(model: ReportModel) -> list[str]:
    lines = _block("策略师全文", model.market_view_raw)
    l3 = _l3_lines(model.finals_rows)
    lines += l3 if l3 else [f"**L3 逐票**:{ABSENT}", ""]
    lines += _lines_block("Tier-3 多空辩论", model.verify_detail_lines)
    return lines


# ── D. 门柱与资格 ───────────────────────────────────────────────────────
def _section_gates(model: ReportModel) -> list[str]:
    return _block("OW 三门失守分布", model.gate_histogram_block, self_labeled=True) + [
        "- 本节直方图分母 = 可解析卡(`gate_status` 解析卡片自由文本);"
        "🧭 仪表盘 ③ 分母 = 满卡(`decision_records.gate_states` 结构化字段)。",
        f"- 两数不等属正常,以结构化那侧为准 → [门柱口径](#{METHOD_ANCHORS['gate']})",
        "- BUY 资格与门柱权威值见 `trace/decision_records.json`,本页不复制。",
        "",
    ]


# ── E. 运行观测 ─────────────────────────────────────────────────────────
def _section_runtime(model: ReportModel) -> list[str]:
    start, end = RUN_OBSERVATION_DETAIL_MARKERS
    # managed 块**先留空**:初次 publish 与 post_run observe 从同一个 observation 注入
    # (§6.5 双刷新)。缺标记会让注入静默 no-op,所以 marker 必须恰好各出现一次。
    return _lines_block("各阶段耗时 & 落盘字节", model.timing_lines) + [start, end, ""]


# ── F. 方法与口径 ───────────────────────────────────────────────────────
def _section_methods(_model: ReportModel) -> list[str]:
    """遍历 `FOOTNOTES` 展开注文正文。锚 id 走 `METHOD_ANCHORS`,**不按出现顺序编号**
    —— 节序一变编号就漂移,summary 里的语义链接会指错地方(§6.9)。"""
    out: list[str] = []
    for key, title, text in FOOTNOTES:
        out += [f'<a id="{METHOD_ANCHORS.get(key, f"method-{key}")}"></a>',
                f"### {title}", "", text, ""]
    return out or [ABSENT, ""]


# ── G. 诚实局限 ─────────────────────────────────────────────────────────
def _section_limitations(model: ReportModel) -> list[str]:
    out = [f"- {x}" for x in HONEST_LIMITATIONS]
    out.append(f"- {FUNNEL_DISCLAIMER}")
    out.append("")
    trace = (f"_明细 + 漏斗溯源:`reports/scan/{model.folder}/`"
             "(summary.md + appendix.md + details/〈名称〉.md + trace/;"
             "目录名=运行时刻,数据日见 manifest.json)_") if model.folder \
        else f"明细 + 漏斗溯源:{ABSENT}"
    out += [trace, ""]
    return out


_SECTION_RENDERERS = {
    "self_review": _section_self_review,
    "funnel": _section_funnel,
    "research": _section_research,
    "gates": _section_gates,
    "runtime": _section_runtime,
    "methods": _section_methods,
    "limitations": _section_limitations,
}


def render_appendix(model: ReportModel) -> str:
    """`ReportModel` → `appendix.md` 全文(纯函数;同一 model 连渲两次字节相同)。

    A–G 七节的标题与 `<a id>` 锚**恒在**,顺序固定;条件素材缺席印 `ABSENT`。
    """
    out = [f"# 扫描附录 — {model.analysis_date or '—'}(run {model.folder or '—'})", "",
           "本页 = `summary.md` 的现场展开;权威值见 "
           "`_final_ratings.json` / `decision_records.json` / `details/` / `trace/`。", ""]
    for key, title in APPENDIX_SECTIONS:
        out += [f'<a id="{APPENDIX_ANCHORS[key]}"></a>', f"## {title}", ""]
        out += _SECTION_RENDERERS[key](model)
    return "\n".join(out).rstrip("\n") + "\n"


def appendix_budget_warn(text: str) -> str | None:
    """超 `APPENDIX_WARN_BYTES` → 一行展示层 warn 文案;否则 None。**不截断**。

    「一份人类可读附录排版超限是展示层问题;报告说假话才是硬门该拦的事」(§6.10)——
    所以这里只回文案,不改内容、不改评级、不毙 GATE4。
    """
    n = len((text or "").encode("utf-8"))
    if n <= APPENDIX_WARN_BYTES:
        return None
    return (f"⚠️ appendix.md {n}B 超展示层上限 {APPENDIX_WARN_BYTES}B"
            f"(+{n - APPENDIX_WARN_BYTES}B);不截断,精简 C 节 L3 全文即可回到预算内。")


__all__ = ["FUNNEL_DISCLAIMER", "HONEST_LIMITATIONS", "appendix_budget_warn", "render_appendix"]
