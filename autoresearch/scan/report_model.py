#!/usr/bin/env python3
"""L5 报告的**不可变事实模型** —— 「先冻结事实、再纯渲染」的单一事实源。

design: docs/specs/2026-08-28-summary-slimdown-design.md §4.1.2 / §6.1(方案 B+)

## 为什么有这个模块

旧 `report_sections.build_summary` 一个函数同时干四件事:读盘 → 评级 fold + 落盘 →
整形 → 渲染 Markdown。于是「同一事实被多个渲染入口各自重读 / 重算」成了结构性风险
(08-26 实测:regime/温度/定调印 3 次、0买机制印 2 次、门柱两个口径同屏打架)。

B+ 把它切成三段,**时序不变、业务计算不变**:

    结构化 staging / details / managed sources
                      ↓ prepare_report_model() 仅一次(读盘 + 既有 finalize/落盘副作用)
                 immutable ReportModel
                      ├─ render_summary(model)   → 决策层 summary.md
                      └─ render_appendix(model)  → 现场层 appendix.md

**两个 renderer 是纯函数**:不读盘、不写盘、不做 rating fold、不重算评级。
appendix 是同一模型的诊断展开视图,**不是第三份权威事实源** —— 权威仍是
`_final_ratings.json` / `decision_records.json` / `details/` / `trace/`。

## 事实归属(§4.1.1;渲染层必须遵守)

| 事实 | summary 唯一展开点 | appendix |
|---|---|---|
| 报告身份 / 数据日 / run id | H1 + 身份行 | — |
| regime / 温度 / 策略师定调 | 🧭 仪表盘① | C(策略师全文) |
| BUY / BLOCKED / 0买结论 | 🧭 仪表盘③ | D |
| 持仓总动作 | 🧭 仪表盘④ | — |
| 仓位 / 新开仓 / 同链 / 分歧 | 行动 | — |
| 单票评级 / 目标 / 短依据 / 漏斗位次 | 候选 / 保送表 | C(L3 全文) |
| 无 BUY 因果 / BUY 资格约束 | 节 6 | D |
| 市场结构 / 红黑榜 / 关注 | 市场地形(策略师 2/3/5 节) | C |
| 漏斗现场 / 卡点 / 门柱自由文本口径 | **不进** summary | B / D |
| 墙钟 / 调用数 / 计量 / 降级 | 运行事实一行 | E |

渲染层禁止把上表右侧的内容再塞回 summary,也禁止在 summary 里第二次展开左侧事实。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 字节预算(§6.10)────────────────────────────────────────────────────
# 这四个数是**展示层预算**:测试锁 warn 上限,运行期在「所有 managed 注入完成后的
# 最终文件」上计量并落展示层 warn。**不截断、不改评级、不毙 GATE4** ——
# 「一份人类可读摘要排版超限是展示层问题;报告说假话才是硬门该拦的事」(GATE3 差 16 字节疤)。
SUMMARY_TARGET_BYTES = 12 * 1024
SUMMARY_WARN_BYTES = 16 * 1024
APPENDIX_TARGET_BYTES = 20 * 1024
APPENDIX_WARN_BYTES = 24 * 1024

#: 兼容别名:旧 `report_sections.SUMMARY_MAX_BYTES` 的语义(单侧上限断言)现由 WARN 承担。
SUMMARY_MAX_BYTES = SUMMARY_WARN_BYTES

# ── 一句依据长度(§6.7;全链唯一上限,不再 80/96 两套)──────────────────
EVIDENCE_MAX_CHARS = 80

# ── appendix 锚(§4.2;ASCII id,跨 Markdown renderer 稳定)──────────────
APPENDIX_FILENAME = "appendix.md"
APPENDIX_ANCHORS: dict[str, str] = {
    "self_review": "appendix-a-self-review",
    "funnel": "appendix-b-funnel",
    "research": "appendix-c-research",
    "gates": "appendix-d-gates",
    "runtime": "appendix-e-runtime",
    "methods": "appendix-f-methods",
    "limitations": "appendix-g-limitations",
}
#: F 节内的方法锚(summary 正文只引语义 key,不按出现顺序分配编号 —— 节序一变编号就漂移)
METHOD_ANCHORS: dict[str, str] = {
    "gate": "method-gate",
    "sector-top3": "method-sector-top3",
    "evidence": "method-evidence",
    "buy-source": "method-buy-source",
    "funnel": "method-funnel",
}

#: appendix 结构节(标题恒在;条件素材缺席印 `无 / NOT_EXPECTED`,**不靠删整节表达 absence**
#: —— 否则「缺任一节」检查会把 sentinel / 无 Tier-3 的合法缺席误报成丢件)
APPENDIX_SECTIONS: tuple[tuple[str, str], ...] = (
    ("self_review", "A. 自检明细"),
    ("funnel", "B. 漏斗现场"),
    ("research", "C. 研究全文"),
    ("gates", "D. 门柱与资格"),
    ("runtime", "E. 运行观测"),
    ("methods", "F. 方法与口径"),
    ("limitations", "G. 诚实局限"),
)

#: 缺席时的显式标记(不是空字符串 —— 空字符串分不清「没有」与「没渲染」)
ABSENT = "_无 / NOT_EXPECTED_"

# ── 运行观测 managed 标记(§6.5 post-run 双刷新;两块同一个 observation 对象)──────
#: summary 侧:紧凑一行(墙钟/调用/计量/降级 + appendix 链接)
RUN_OBSERVATION_MARKERS = ("<!-- run-observation:start -->", "<!-- run-observation:end -->")
#: appendix E 侧:完整块(成本表 / 成熟度 / 预算状态)
RUN_OBSERVATION_DETAIL_MARKERS = ("<!-- run-observation-detail:start -->",
                                  "<!-- run-observation-detail:end -->")


def method_link(text: str, key: str) -> str:
    """summary 正文里的稳定语义口径链接(§6.9)。`key` ∈ METHOD_ANCHORS。"""
    return f"[{text}]({APPENDIX_FILENAME}#{METHOD_ANCHORS[key]})"


def appendix_link(text: str, key: str) -> str:
    """summary 正文里指向 appendix 某节的稳定链接。`key` ∈ APPENDIX_ANCHORS。"""
    return f"[{text}]({APPENDIX_FILENAME}#{APPENDIX_ANCHORS[key]})"


#: 口径注文单一事实源(§6.9):summary 只放语义链接,注文正文集中 appendix F。
#: 值 = (锚 key, 标题, 注文)。新增口径只改这里 + appendix F 自动展开。
FOOTNOTES: tuple[tuple[str, str, str], ...] = (
    ("gate", "门柱口径",
     "🧭 仪表盘 ③ 与本附录 D 的门柱来自**两个不同生产者**:仪表盘走 "
     "`decision_records.gate_states` **结构化字段**、分母 = 满卡;附录 D 的直方图走 "
     "`gate_status` 解析**卡片自由文本**、分母 = 可解析卡。**两个数不等是正常的,"
     "以结构化那侧为准**(自由文本解析器有漏读加粗 `**✗**` 的前科,同族已复发三次)。"),
    ("sector-top3", "行业资格门",
     "资格门:n≥8 ∧ 资金门(主力净比中位>0 或 为正占比≥50%)∧ 非落刀(60日中位>−20%);"
     "四组件 rank-sum 等权(资金/健康占比/估值/倒U动量)。**无论点**;证伪点 = 分数构成反转。"
     "只进 L5 与 sector_ledger,**不喂 L3/L4**。"),
    ("evidence", "一句依据",
     "候选/保送表的「一句依据」= **与终评级同向**的证据:≥OW 只取多头段(L4 一行多空的多侧 / "
     "L3 thesis);≤Hold 只取空头段(L4 空侧 / 早停因 / 失守门柱 / L3 risk)。"
     "**反向段不作为回退**(宁缺毋误导),全空印 `—`。全文见 `details/` 与本附录 C。"),
    ("buy-source", "BUY 的事实源",
     "BUY 出自 `_relative_buy_decision.json` 的 `buys[]`(E6 相对决策层**独家拥有**);"
     "研究评级 ≥OW 的张数是**证据不是决策**。相对 BUY 只承诺「当日可交易全集里相对最优」,"
     "**不承诺绝对收益为正** —— 这与「当天亏钱」可以同时为真。结论只在 🧭 仪表盘③ 展开一次。"),
    ("funnel", "漏斗口径",
     "L1 复合分与 L2 sn_composite 同口径(主尺 IC 校准);L2 为确定性分层采样,"
     "文件名/列名里的 `gbdt` 是遗留别名,**不是模型**。"),
)


@dataclass(frozen=True)
class ReportModel:
    """L5 一次整形后的不可变事实模型。**渲染层只读它,不再回头读盘**。

    字段分四类:身份 / 决策 / 现场 / 已渲染片段。「已渲染片段」是既有确定性生产者
    (calendar / market / menu / run_mode …)直接产出的 Markdown 块 —— 本波不重写它们,
    只决定它们落在 summary 还是 appendix。
    """

    # ── 身份 ────────────────────────────────────────────────────────────
    analysis_date: str              #: 数据日 YYYY-MM-DD
    hhmm: str                       #: 发布时刻 HHMM
    folder: str                     #: 发布目录名(run_id)
    meta: dict[str, Any] = field(default_factory=dict)

    # ── 决策(评级 fold 已在 prepare 阶段完成,这里是终值)────────────────
    rows: list[dict] = field(default_factory=list)          #: 全部(genuine + pinned),已排序
    genuine_rows: list[dict] = field(default_factory=list)  #: 真实精选 → 候选表
    pinned_rows: list[dict] = field(default_factory=list)   #: 📌 保送 → 保送表
    vmap: dict[str, dict] = field(default_factory=dict)     #: Tier-3 verify
    emap: dict[str, dict] = field(default_factory=dict)     #: 买单复核 ensemble
    conflicts: Any = None                                   #: tripwire_conflicts 结果

    # ── 漏斗位次(候选表 L1→L2 列)────────────────────────────────────────
    l1_full: dict[str, dict] = field(default_factory=dict)
    l2_top: dict[str, dict] = field(default_factory=dict)
    ch_map: dict[str, str] = field(default_factory=dict)
    n_l1: Any = "?"
    n_l2: Any = "?"
    recall_rows: list[dict] = field(default_factory=list)   #: L1_recall_top1000.csv
    keep_rows: list[dict] = field(default_factory=list)     #: L2_gbdt_top200.csv
    finals_rows: list[dict] = field(default_factory=list)   #: finalists.csv 原行(appendix C 用 risk/catalyst)

    # ── 已渲染片段(生产者直出;presence-gated,缺 → "")─────────────────
    regime_line: str = ""
    regime_drift: Any = None
    temp_line: str = ""
    calendar_block: str = ""
    #: minor-7(final whole-branch review):`calendar.index_rebalance` 旋钮的镜像位——
    #: `render_summary` 是纯函数(不读盘),不能自己去 `knob()`,所以在 prepare 阶段(唯一允许
    #: I/O 的地方)读一次、原样带过来,渲染层只用它挑标题措辞,不判断"为什么"。缺省 `False`
    #: = 旧标题(旋钮关的 parity),与代码内建默认一致。
    index_rebalance_enabled: bool = False
    portfolio_block: str = ""       #: managed 占位或 legacy 文本
    overlay_block: str = ""         #: managed 占位或 legacy 文本
    run_mode_banner: str = ""
    same_chain_block: str = ""
    ensemble_dissent_lines: list[str] = field(default_factory=list)
    pinned_section: str = ""        #: 旧 `_pinned_section` 全文(含 ⚖️ 两尺分歧)
    market_view_raw: str = ""       #: 策略师 market_view.md 原文(缺 → "")
    market_view_slices: dict[str, str] = field(default_factory=dict)
    market_view_parse_ok: bool = True
    fallback_pulse: str = ""        #: 无策略师稿时的确定性脉搏
    funnel_readout: str = ""        #: 📉 今日漏斗读数(0买机制)
    sector_top3_block: str = ""
    funnel_table_lines: list[str] = field(default_factory=list)
    degraded_line: str = ""
    stage_overview_lines: list[str] = field(default_factory=list)
    menu_health_block: str = ""
    timing_lines: list[str] = field(default_factory=list)   #: 各阶段耗时 & 落盘字节(全表)
    gate_histogram_block: str = ""  #: 自由文本口径直方图(→ appendix D)
    verify_detail_lines: list[str] = field(default_factory=list)
    overseas_calendar_lines: list[str] = field(default_factory=list)  #: D-2 预留(外源稿)

    # ── 身份行与 BUY 资格(§4.1 节 1 / 节 6;prepare 阶段算好)──────────────
    identity_line: str = ""         #: 「数据截至 … · 发布 …」—— 只放报告身份,不放结论
    buy_constraint_lines: list[str] = field(default_factory=list)
    buy_constraint_title: str = "## 为什么没有 BUY"

    # ── 自检 ───────────────────────────────────────────────────────────
    #: prepare 阶段冻结的 review 输入(finals / n_cards / flow / _extras 六条不纯 lint 结果)
    review_ctx: dict[str, Any] = field(default_factory=dict)
    #: `attach_review()` 回填:对**最终 summary 正文**跑完 review 的结果(含空泛话术检)
    review_result: dict[str, Any] = field(default_factory=dict)
    #: `attach_review()` 回填:**非聚合**全文 banner(appendix A 用;summary 用聚合版)
    banner_full: str = ""


__all__ = [
    "ABSENT", "APPENDIX_ANCHORS", "APPENDIX_FILENAME", "APPENDIX_SECTIONS",
    "APPENDIX_TARGET_BYTES", "APPENDIX_WARN_BYTES", "EVIDENCE_MAX_CHARS",
    "FOOTNOTES", "METHOD_ANCHORS", "ReportModel", "SUMMARY_MAX_BYTES",
    "SUMMARY_TARGET_BYTES", "SUMMARY_WARN_BYTES", "appendix_link", "method_link",
]
