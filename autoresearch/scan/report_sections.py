"""Pure scan summary sections and report composition.

## 节序与事实归属(§4.1 / §4.1.1 真身;改序先改这里)

`summary.md` = **决策层**(11 节),`appendix.md` = **现场层**(A–G,见 `report_appendix.py`)。
两份都由**一次** `prepare_report_model()` 冻结出的 `ReportModel` 纯渲染而来。

```
0  自检 banner(聚合版;明细 → appendix A)
1  H1 + 身份行(数据日 / run id / 发布时刻;**不放结论**)
2  🧭 决策仪表盘(managed;brief ①②③④ 逐字)
3  ## 行动(overlay 仓位 · 组合集中度 · 同链 1-bet · 复核分歧 · 哨兵 banner)
4  ## 候选(N 只)   # | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2
5  ## 📌 保送持仓
6  ## 为什么没有 BUY / ## BUY 资格与约束(单源 decision_records)
7  ## 市场地形(策略师 2/3/5 节切片;全文 → appendix C)
8  ## 行业 top3
9  ## 📅 未来 14 天
10 ## 运行事实(managed 紧凑一行;完整块 → appendix E)
11 ## 诚实局限(一行 + appendix G;锚字面 "\n## 诚实局限" 是 💸 注入回退锚,勿动)
```

**事实归属**(每个事实在 summary 里只有一个展开点,详见 `report_model.ReportModel` docstring):
regime/温度/定调 → 仪表盘①;BUY/BLOCKED 结论 → 仪表盘③;持仓总动作 → 仪表盘④;
仓位与动作 → 节 3;单票 → 节 4/5;无 BUY 的**统计** → 节 6;漏斗现场/门柱自由文本口径 → appendix。

## 文风契约(§4.3)

1. 数字先行;2. 一行一事实;3. 夹注下沉(正文不出现「口径:」「注:」「由来:」,
用 `report_model.method_link()` 的稳定语义链接指向 appendix F);4. 同一事实只印一次;
5. 恒定模板文本(完整局限 / token 说明 / 列注 / 方法段)只进 appendix 或 index.md;
6. 候选表只有「一句依据」一列自由文本,`EVIDENCE_MAX_CHARS=80` 确定性截断。
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from pathlib import Path

from autoresearch.common.ruler import MAIN_RULER
from autoresearch.scan.decision_finalize import (
    _PROPOSAL_BY_RATING,
    _VERDICT_BADGE,
    BLIND_CARD_TARGET,
    TIER_RANK,
    _apply_ensemble_fold,
    _apply_verify_downgrade,
    _dump_decision_records,
    _dump_final_ratings,
    _ensemble_dissent_lines,
    _ensemble_flag,
    _load_ensemble,
    _load_verify,
    _verify_badge,
    build_dissent_records,
    dump_dissent_records,
)
from autoresearch.scan.l4.parsers import (
    _GATES3,
    _decision_text,
    _finalist_row,
    _load_json,
    _read_csv,
    _strip,
    gate_status,
)
from autoresearch.scan.relative_buy import DECISION_FILENAME, is_active
from autoresearch.scan.report_model import (
    APPENDIX_TARGET_BYTES,
    APPENDIX_WARN_BYTES,
    EVIDENCE_MAX_CHARS,
    FOOTNOTES,
    RUN_OBSERVATION_MARKERS,
    SUMMARY_MAX_BYTES,
    SUMMARY_TARGET_BYTES,
    SUMMARY_WARN_BYTES,
    ReportModel,
    appendix_link,
    method_link,
)

#: 兼容再导出:`SUMMARY_MAX_BYTES` 等名字历史上住在本模块,现在**单一事实源在 `report_model`**。
#: 这行让 ruff 认得这些 import 是有意的对外面(不是没用到的死 import)。
__all_report_model_reexports__ = (APPENDIX_TARGET_BYTES, APPENDIX_WARN_BYTES,
                                  FOOTNOTES, SUMMARY_MAX_BYTES, SUMMARY_TARGET_BYTES)

_CH_ZH = {
    "composite": "复合",
    "momentum": "动量",
    "reversal": "反转",
    "growth": "成长",
    "value": "价值",
    "main_fund": "主力",
    "northbound": "北向",
    "accumulation": "吸筹",
    "heat": "热度",
}


def _verify_detail(vmap: dict[str, dict]) -> list[str]:
    """Tier-3 多空辩论明细块:降级/否决 摊开 多/空/触发/共识(维持的不赘述);vmap 空 → [](老路不破)。"""
    if not vmap:
        return []
    n = {k: sum(1 for v in vmap.values() if v["verdict"] == k) for k in ("维持", "降级", "否决")}
    lines = ["", f"### 🛡️ Tier-3 买单多空辩论({len(vmap)} 只:{n['维持']} 维持 / {n['降级']} 降级 / {n['否决']} 否决)"]
    hits = [(c, v) for c, v in vmap.items() if v["verdict"] in ("降级", "否决")]
    if hits:
        for c, v in hits:
            bull = f"多:{v['bull']};" if v.get("bull") else ""
            trig = f" ｜ 触发:{v['trigger']}" if v.get("trigger") else ""
            cons = f" ｜ 共识:{v['consensus']}" if v.get("consensus") else ""
            lines.append(f"- **{c}** {_VERDICT_BADGE.get(v['verdict'], v['verdict'])}:{bull}空:{v['bear']}{trig}{cons}")
    else:
        lines.append("- 全部维持:多空辩论后空头未拿出证伪买点的硬证据。")
    return lines

#: 「本行口径」标注(fix-1,复核 I-5)。**两个门柱生产者同屏,必须各自自报口径**:
#: 本节走 `gate_status` 解析**卡片自由文本**,🧭 仪表盘 ③ 走 `decision_records.gate_states`
#: **结构化字段**,人口(可解析卡 vs 满卡)与解析方式都不同,数会不等。
#: `gate_status` 正是 memory 里「读不懂加粗 `**✗**` → 17.4% 的卡被判三门全过」的同族解析器,
#: 已复发三次 —— 所以标注里直接写明**以结构化那侧为准**,不让读者随机相信一个。
GATE_HIST_BASIS_NOTE = ("_口径:本行由 `gate_status` 解析**卡片自由文本**得来,分母=可解析卡;"
                        "🧭 仪表盘 ③ 的门柱走 `decision_records.gate_states` **结构化字段**、"
                        "分母=满卡,**两个数不等是正常的,以结构化那侧为准**"
                        "(自由文本解析器有漏读加粗 `**✗**` 的前科)。_")


def gate_histogram(scan_dir: Path, rows: list[dict]) -> str:
    """OW三门失守分布一行(确定性,逐卡数 `OW三门 …` 段的 ✗)。0买日一行看懂"今天为什么没买"
    ——胜过读 30 格被截断的结论;有买日同样给出门柱形状。无可解析卡 → ''。

    **必须带 `GATE_HIST_BASIS_NOTE`**(I-5):T26 把 brief ③ 注进 summary 的仪表盘后,
    同一页会同时印两个门柱数(实测 08-06:本行「主力真在✗ 4」vs 仪表盘「主力真在 5」)。
    """
    cnt = dict.fromkeys(_GATES3, 0)
    parsed = 0
    for r in rows:
        text = _decision_text(scan_dir, str(r.get("code", "")).zfill(6)) or ""
        st = gate_status(text)
        if st is None:
            continue
        parsed += 1
        for g, failed in st.items():
            if failed:
                cnt[g] += 1
    if not parsed:
        return ""
    parts = " · ".join(f"{g}✗ {cnt[g]}" for g in _GATES3)
    return (f"**OW三门失守分布**({parsed} 卡可解析):{parts}"
            f"(任一门✗ 即压 ≤Hold;门柱即当日 0买/有买的结构性原因)\n\n"
            f"{GATE_HIST_BASIS_NOTE}")

_gate_histogram = gate_histogram

def _sortkey(r: dict):
    tier = TIER_RANK.get(r.get("rating", ""), 99)
    try:
        conv = float(r.get("conviction") or 0)
    except ValueError:
        conv = 0.0
    return (tier, -conv)

def _funnel_rows(meta: dict, n_l2, n_l3, n_cards, n_pinned: int = 0) -> list[str]:
    l2_eng = meta.get("l2_engine", "stratified")
    l3_out = f"{n_l3} (+{n_pinned} 保送直通)" if n_pinned else f"{n_l3}"   # 保送不占 L3 名额,单列标注
    return [
        "| 阶段 | 名称 | 出量 | 引擎 | 卡点标准 |", "|---|---|---:|---|---|",
        f"| L0 | 选集 | {meta.get('universe', '?')} | 确定性 | 全A {meta.get('universe_raw', '?')} → 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
        f"| L1 | 召回 | {meta.get('recall_n', '?')} | 确定性 | 轻门 + 行业条件化复合分({MAIN_RULER} 超短主尺 IC 校准) top |",
        f"| L2 | 粗排 | {n_l2} | 分层采样/{l2_eng} | 确定性分层采样(sn_composite 排序+风格桶 floor+sector cap;零模型零 LLM;文件名/列名 gbdt 为遗留别名) |",
        f"| L3 | 精排 | {l3_out} | Opus·max·holistic | 1 agent 通看 ~200 比较选 + 增量证据/论点/红队(保送票不占名额) |",
        f"| L4 | 研究 | {n_cards} 卡 | Opus·medium | 一只=一个 Opus subagent 渐进深度 DD + 早停 |",
    ]

def _channels_zh(s) -> str:
    """recall_channels 串('growth|heat')→ 中文短标('成长/热度');回填/空 → 标注。

    注:分隔符用 '/' 而非 '|' —— '|' 会被 markdown 表格当成列分隔符、把单元格劈成两列(列错位)。
    """
    if not isinstance(s, str) or not s:
        return "—"
    if s == "(backfill)":
        return "回填"
    return "/".join(_CH_ZH.get(c, c) for c in s.split("|"))

def _l1_cell(code: str, l1_full: dict[str, dict], ch_map: dict[str, str]) -> str:
    """L1 召回结论:#复合分名次(分母在列头)· 命中队列(哪几路召回)。"""
    r = l1_full.get(str(code).zfill(6))
    if not r:
        return "—"
    return f"#{r.get('rank', '?')}·{_channels_zh(ch_map.get(str(code).zfill(6), ''))}"

def _l2_cell(code: str, l2_top: dict[str, dict]) -> str:
    """L2 粗排结论:#L2 重排名次(分母在列头)· gbdt 分(列名为遗留别名,值 = sn_composite 分层采样序)。"""
    r = l2_top.get(str(code).zfill(6))
    if not r:
        return "—"
    g = r.get("gbdt_score")
    try:
        gtxt = f"·g{float(g):.2f}" if g not in (None, "", "nan") else ""
    except (TypeError, ValueError):
        gtxt = ""
    return f"#{r.get('l2_rank', '?')}{gtxt}"

def _stage_token_estimate(scan_dir: Path) -> list[str]:
    """分阶段耗时 + LLM 调用数 + 落盘字节(确定性,无 LLM)。**本表不再估算 token**。

    历史与退役理由(Wave6 T8):本表曾用「落盘字节 ÷ 2.8」估 token,2026-07-24 那份报告
    因此写 ~183,623;对**同一次跑动**做 transcript 追溯真计量得到 **加权 5.49M /
    billed 22.4M / 输出 716.6k** —— 对加权低估 **30 倍**,且分布与旧假设相反(L3 真占
    7.8% 而非 37%,大头在主会话编排 27% 与 L4 卡 23%)。这张表是「第二刀砍哪里」的决策
    输入,错 30 倍比没有更糟,故估算列整列退役。

    保留的都是硬事实:分段墙钟(mtime 推导)、LLM 调用数、落盘字节。真实用量以 CP7 的
    `token_usage.md`(`trace.usage_harvest`,按计价倍率加权)为唯一正典。
    """
    det = scan_dir

    def _b(files) -> int:
        return sum(p.stat().st_size for p in files if p.is_file())

    cards = sorted((det / "details").glob("*.md")) if (det / "details").is_dir() else []
    strat = ([det / "market_view.md"] if (det / "market_view.md").is_file() else []) \
        + list(det.glob("_strategist*"))
    sbriefs = (sorted((det / "sector_briefs").glob("*.md"))
               if (det / "sector_briefs").is_dir() else [])
    l3 = list(det.glob("_l3*")) \
        + ([det / "L3_judged_full.csv"] if (det / "L3_judged_full.csv").is_file() else [])
    l4t1 = list(det.glob("_l4_batch*")) + list(det.glob("_l4_prompt*"))
    # L4 输入侧最大件 = slim(context/<ticker>_<date>_slim.md,scan_dir 通常是 context/scan/<date>)
    slim_root = det.parent.parent
    slims = sorted(slim_root.glob(f"*_{det.name}_slim.md")) if slim_root.exists() else []
    intels = sorted(det.glob("_l4_intel_*.md"))   # 活体情报(l4-intel 盲搜落稿;config 未启用/未派 → 空)
    from autoresearch.scan.stage_timing import ensure_stage_timing
    _tmap: dict = ensure_stage_timing(det)   # mtime 推导补缺 + 写回;编排写过的 key 优先

    def _wall(key: str) -> str:
        v = _tmap.get(key)
        if isinstance(v, dict):
            v = v.get("wall_s")
        if not v:
            return "—"
        v = int(v)
        return f"{v // 60}m{v % 60:02d}s" if v >= 60 else f"{v}s"

    # P6b:effort/引擎列改读 `user_config_echo.json`(frame --json 落的当日实际调用配置)——
    # 此前是硬编码现值,和真实调用(如 L4 xhigh/sonnet·high)脱节,表面 medium 掩盖了真实档位。
    # presence-gated:无 echo 文件/字段缺失 → 回退旧硬编码值(parity,原表不变)。
    echo_agents: dict = {}
    try:
        import json as _json
        echo_agents = (_json.loads((det / "user_config_echo.json").read_text(encoding="utf-8"))
                       .get("agents") or {})
    except Exception:  # noqa: BLE001 — 无 echo = 旧硬编码现值(parity)
        echo_agents = {}

    def _eff(key: str, default: str) -> str:
        v = (echo_agents.get(key) or {}).get("effort")
        return str(v) if v else default

    def _eng(key: str, default: str) -> str:
        m = (echo_agents.get(key) or {}).get("model")
        return {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}.get(str(m).lower(), str(m)) \
            if m else default

    ens_files = sorted((det / "ensemble").glob("*.md")) if (det / "ensemble").is_dir() else []
    has_prewarm = (det / "_prewarm.json").is_file()

    # (阶段名, 引擎, effort, 计时键, LLM调用, 落盘字节, 说明)
    rows = [
        *([("预热(夜间)", "确定性", "—", "预热", 0, 0, "lake/evidence/温度预拉(_prewarm.json)")]
          if has_prewarm else []),
        ("L0/L1/L2", "确定性", "—", "L0L1L2", 0, 0, "纯 pandas,零 LLM"),
        ("旁路 策略师", _eng("strategist", "Opus"), _eff("strategist", "session"), "策略师",
         1 if strat else 0, _b(strat), "market_pack → market_view.md"),
        ("旁路 行业brief", _eng("sector_brief", "Opus"), _eff("sector_brief", "low"), "行业brief",
         len(sbriefs), _b(sbriefs), "sector pack → sector_briefs/*.md(♻️TTL 复用亦计字节)"),
        ("L3 精排", _eng("l3_rank", "Opus·holistic"), _eff("l3_rank", "max"), "L3精排",
         1 if l3 else 0, _b(l3), "通看全表选 finalists(输入表落 `_l3_table.md` 才计入)"),
        ("L4 研究", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "L4研究", len(cards),
         _b(cards) + _b(l4t1), f"{len(cards)} 张卡(早停/满卡/复用;每卡 prompt 落 `_l4_prompt_*` 才计入)"),
        *([("L4 买单ensemble", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "ensemble",
            len(ens_files), _b(ens_files), "≥OW 追加 run2/3 取中位(仅有买日)")] if ens_files else []),
        ("L4 输入·slim", "—(输入侧)", "—", "L4slim", len(slims), _b(slims),
         "harvest --slim 落稿(每卡 subagent 读入;≈4.8KB 空稿=NO_DATA 亦计=真实浪费)"),
        ("L4 输入·情报", _eng("l4_intel", "Sonnet"), _eff("l4_intel", "max"), "L4intel",
         len(intels), _b(intels),
         "l4-intel 盲搜落稿(1 文件=1 sonnet 会话;网查用量见 `token_usage.md`;未启用=0;不计入 LLM 调用合计)"),
        ("L4 新闻网查", "WebSearch", "—", "L4news", 0, 0,
         "P3 有界活体新闻(≤3/卡)+ sector/macro 网查(≤2)——无落盘 artifact,用量见 `token_usage.md`"),
        ("整合 assemble", "确定性", "—", "assemble", 0, 0, "L5 组装 + self_review(截至本表渲染)"),
    ]
    lines = ["## 各阶段耗时 & 落盘字节",
             "| 阶段 | 引擎 | effort | 墙钟 | LLM 调用 | 落盘字节 | 说明 |",
             "|---|---|---|---:|---:|---:|---|"]
    tot_calls = 0
    for name, eng, eff, tkey, calls, b, note in rows:
        tot_calls += 0 if name.startswith("L4 输入") else calls
        lines.append(f"| {name} | {eng} | {eff} | {_wall(tkey)} | {calls or '—'} | {b or '—'} | {note} |")
    lines.append(f"| **合计** | — | — | {_wall('总计')} | **{tot_calls}** | — | 墙钟 = mtime 推导下界(stage_timing.py) |")
    if cards and not list(det.glob("_l4_prompt*")):
        lines += ["", "> ⚠️ L4 输入 prompt 未落稿(`_l4_prompt_*` 缺)——上表 L4 行仅计输出;派发前先 "
                  "`uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>` "
                  "落稿,输入侧才可计。"]
    lines += ["", "> **token 计量**:本表**不估 token** —— 旧「落盘字节 ÷ 2.8」口径于 2026-07-24 "
              "被同一次跑动的 transcript 追溯真计量证伪(估 ~183.6k vs 加权真值 5.49M,**低估 30 倍**,"
              "且把「贵在哪」排反)。真实用量见 CP7 产出的 `token_usage.md`"
              "(`python -m autoresearch.trace.usage_harvest --session <sessionId> --out …`,按计价倍率加权)。"
              "**该文件不存在 = 本次未计量,不等于用量小。**"
              "上表三列(墙钟/调用数/落盘字节)是确定性硬事实,可直接引用。", ""]
    return lines

def _stage_overview(label: str, rows: list[dict], reason: str) -> list[str]:
    if not rows:
        return [f"\n**{label}** — _无 staging,跳过_"]
    inds = Counter(r.get("industry", "") for r in rows if r.get("industry"))
    top = "、".join(f"{k}({v})" for k, v in inds.most_common(5)) or "—"
    reps = ", ".join(str(r.get("name", "")) for r in rows[:6])
    return [f"\n**{label}** — {reason}", f"- 行业分布 top5:{top}", f"- 代表股:{reps}"]

# ── 🧭 决策仪表盘(managed 块;T26 §C2「仪表盘 = brief ①②③④ 同源渲染」)────────────
#
# **为什么是 managed 占位而不是就地渲染**:③ BUY 结论区要读 `_relative_buy_decision.json`,
# 而那份文件由 `post_run.publish_run_observation` → `relative_buy.safe_write_decision` 写,
# 它**必须**排在 `build_summary` 之后 —— finalizer 现算护照,护照要读 `decision_records.json`,
# 而 decision_records 恰恰是 build_summary 内部才落的(顺序颠倒会让 finalizer 判 BLOCKED,
# 见 relative_buy.py I-1)。所以 assemble 只落占位,publisher 收尾用**与 brief.md 同一份
# facts** 注入 —— 两边同源,T27 的「brief↔summary BUY 一致」lint 才不是自己跟自己对账。
DASHBOARD_START = "<!-- SCAN_DASHBOARD_START -->"
DASHBOARD_END = "<!-- SCAN_DASHBOARD_END -->"
DASHBOARD_HEADER = "## 🧭 决策仪表盘(与 `brief.md` ①②③④ 同源)"


def _managed_block(start: str, end: str, header: str | None, body: str) -> str:
    """managed 块的**唯一**成型口径(header 为空 → 不出标题行)。"""
    head = f"{header}\n\n" if header else ""
    return f"{start}\n{head}{body}\n{end}"


def _inject_block(summary: str, start: str, end: str, header: str | None, body: str) -> str:
    """把正文原位替换进 managed 块(幂等)。缺标记 → 原样返回,不猜插入点。

    **缺标记即 no-op 是 shadow parity 的结构性保证**:影子期报告根本不落这些标记,所以
    收尾注入对它一个字节都改不了 —— parity 不靠"记得别调用",靠"调了也没东西可改"。
    """
    if start not in summary or end not in summary:
        return summary
    before, rest = summary.split(start, 1)
    _, after = rest.split(end, 1)
    head = f"{header}\n\n" if header else ""
    return f"{before}{start}\n{head}{body.strip()}\n{end}{after}"


def dashboard_placeholder() -> str:
    return _managed_block(
        DASHBOARD_START, DASHBOARD_END, DASHBOARD_HEADER,
        "_仪表盘由 assemble 收尾注入(与 brief.md 同源);此处为占位——"
        "看到本行说明注入未跑,读 `brief.md`。_")


def inject_dashboard(summary: str, block: str) -> str:
    """把仪表盘正文原位替换进 managed 块(幂等)。缺标记 → 原样返回,不猜插入点。"""
    return _inject_block(summary, DASHBOARD_START, DASHBOARD_END, DASHBOARD_HEADER, block)


# ── E3b:active 期的两个 BUY 数渲染点 → managed 占位 + 收尾注入(task-2.4)──────────
#
# 病灶与仪表盘**同源**:「组合视角」的买单数与「仓位 overlay」的 0买判断,active 期都该
# 出自 `_relative_buy_decision.json` 的 `buys[]`,而那份文件由 writer-1 在
# `publisher.py:325` 才写 —— 比 `build_summary`(`:305`)晚一站。就地读盘 = 读到上一日/
# 上一跑那份(2026-08-19 取证文档 §2:8 份 brief 里 4 份与决策文件不一致就是这个形状)。
# 所以这两处照仪表盘的成方:assemble 只落**自证占位**,`brief.safe_publish`(跑在
# `publisher.py:387`,决策文件已在盘)在**同一次注入**里回填。
#
# 占位文案必须自证(「看到本行说明注入未跑」):注入断链要立刻可见,而不是静默给旧数 ——
# 静默给旧数正是本波要根治的病,不能让防它的机制自己复刻一遍。
PORTFOLIO_START = "<!-- SCAN_PORTFOLIO_START -->"
PORTFOLIO_END = "<!-- SCAN_PORTFOLIO_END -->"
OVERLAY_START = "<!-- SCAN_OVERLAY_START -->"
OVERLAY_END = "<!-- SCAN_OVERLAY_END -->"


def portfolio_placeholder() -> str:
    return _managed_block(
        PORTFOLIO_START, PORTFOLIO_END, None,
        "_组合视角的 BUY 数由 assemble 收尾注入(源=`_relative_buy_decision.json` 的 "
        "`buys[]`);此处为占位——看到本行说明注入未跑,读 `brief.md` ③。_")


def overlay_placeholder() -> str:
    return _managed_block(
        OVERLAY_START, OVERLAY_END, None,
        "_仓位 overlay 由 assemble 收尾注入(0买判断源=`_relative_buy_decision.json`);"
        "此处为占位——看到本行说明注入未跑,读 `brief.md` ③。_")


def _rows_for_injection(scan_dir: Path) -> list[dict]:
    """收尾注入用的 genuine rows(lane≠pinned)。**只读盘上已定稿的产物**,与 build_summary 同源:

    - `sector`/`lane` 出自 `finalists.csv`(运行期烤进去的事实);
    - `rating` 出自 `_final_ratings.json` —— 那正是 `build_summary` 里两个 fold 循环跑完后
      `_dump_final_ratings` 落的**同一份终评级**,不是重新解析卡片再折一遍(重算=给两边
      不一致开口子,与 `brief.dashboard_block` 同源纪律一致)。
    """
    import contextlib

    scan = Path(scan_dir)
    ratings: dict = {}
    with contextlib.suppress(Exception):
        ratings = _load_json(scan / "_final_ratings.json") or {}
    ratings = {str(k).zfill(6): v for k, v in ratings.items()}
    rows = []
    for fr in _read_csv(scan / "finalists.csv"):
        if str(fr.get("lane", "")).strip() == "pinned":
            continue
        code = str(fr.get("code") or fr.get("ticker") or "").strip().zfill(6)
        rows.append({**fr, "code": code, "rating": ratings.get(code, "—")})
    return rows


def _decision_buy_codes(decision: dict) -> set[str]:
    return {str(row.get("code")).zfill(6) for row in (decision.get("buys") or [])
            if isinstance(row, dict) and row.get("code")}


#: 注入时决策文件缺席/过期的**显式回退标记**。这里刻意**不**退回旧 ≥OW 计数 —— 那等于把
#: 研究评级冒充成买入决策(Wave12 `:466` 明令禁止),宁可让报告说「这个数现在不可用」。
_DECISION_UNAVAILABLE = ("⚠️ **BUY 数不可用**:收尾注入时 `_relative_buy_decision.json` "
                         "缺席或日期不符 —— 本行**不回退到旧 ≥OW 计数**(研究评级不是买入决策),"
                         "请查 writer-1 是否跑过。")


def inject_deferred_blocks(summary: str, scan_dir: Path | str,
                           decision: dict | None) -> str:
    """回填 E3b 的两个 active 占位块(组合视角 / 仓位 overlay)。

    缺标记 → 原样返回:影子期报告里根本没有这两个标记,所以本函数在 shadow 期是结构性
    no-op(parity)。`decision` 为 None(缺席/过期)→ 两处都渲染显式回退标记,不给旧数。
    """
    if PORTFOLIO_START not in summary and OVERLAY_START not in summary:
        return summary
    scan = Path(scan_dir)
    rows = _rows_for_injection(scan)
    summary = _inject_block(summary, PORTFOLIO_START, PORTFOLIO_END, None,
                            _portfolio_note_active(rows, decision))
    return _inject_block(summary, OVERLAY_START, OVERLAY_END, None,
                         _position_overlay_active(scan, decision))


def _portfolio_note_from(rows: list[dict], buys: list[dict], label: str,
                         n_buys: int | None = None) -> str:
    """组合视角一行的**唯一**成型口径。legacy(≥OW)与 active(决策文件 buys[])共用它 ——
    两处各写一份渲染,「买单同板块=1个bet」这类告警必然只在一边生效。

    `n_buys` 显式覆盖买单**只数**(active 用):买单数的事实源是决策文件,不是"能在
    `rows` 里匹配上几行" —— 匹配只用来算板块分布。两者不是一回事,见
    `_portfolio_note_active` 的📌案例。
    """
    secs = Counter((r.get("sector") or r.get("industry") or "?") for r in rows)
    top = "、".join(f"{k}×{v}" for k, v in secs.most_common(5))
    del label, n_buys      # 2026-08-29:BUY 只数与其标签归仪表盘③ 独占,这里只出组合形状。
                           # 参数留在签名里是给调用方的兼容面(两个 caller 各自算 n 的口径不同,
                           # 将来若要恢复本行的数,别再各写一份)。
    # 2026-08-29 事实归属(§4.1.1):**BUY 只数归 🧭 仪表盘③ 独占**,本行只出组合形状
    # (集中度 + 相关性告警)。此前同一页会同时出现仪表盘的 BUY 数与这里的 BUY 数,
    # 两个生产者、两条口径,读者随机相信一个 —— 门柱直方图同族疤已经复发过三次。
    # `label`/`n` 仍进签名与告警文案(它们决定「几只买单同板块」怎么说),只是不再单印一遍。
    note = (f"板块集中度:{top or '—'}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,催化日历做节奏。")
    if len(buys) >= 2:                       # 买单同板块 = 1 个 bet 不是 N 个(组合视角告警)
        bsec = Counter((r.get("sector") or r.get("industry") or "?") for r in buys)
        k, v = bsec.most_common(1)[0]
        if v >= 2:
            note += f" **⚠️ {v}/{len(buys)} 只买单同属{k} = 相关性上是 1 个 bet,仓位按 1 个算。**"
    return note


def _portfolio_note(rows: list[dict]) -> str:
    """legacy 口径(≥Overweight 绝对门)。**active 期不再由它渲染** → `_portfolio_note_active`。"""
    return _portfolio_note_from(
        rows, [r for r in rows if r.get("rating") in ("Buy", "Overweight")], "买入/超配")


def _portfolio_note_active(rows: list[dict], decision: dict | None) -> str:
    """active 口径:买单 = 决策文件 `buys[]`,**不是**评级 ≥OW 的张数。

    ⚠️ 只数**决策文件**里的只数,不数"在 `rows` 里匹配上几行"。2026-08-19 真产物实跑逮到:
    08-18 的相对 BUY 688766 本身是📌持仓,而 `rows` 是 genuine(lane≠pinned)—— 按匹配数
    渲染会得到「BUY 0 只」,同屏的 overlay 却说「1 只买单」。这是「下游丢弃上游成果」的
    原样复刻。匹配只服务于板块分布/同板块告警;匹配不上时**显式说出来**,不静默降数。
    """
    if not isinstance(decision, dict):
        return _DECISION_UNAVAILABLE
    codes = _decision_buy_codes(decision)
    buys = [r for r in rows if str(r.get("code", "")).zfill(6) in codes]
    note = _portfolio_note_from(rows, buys, "BUY(相对决策层)", n_buys=len(codes))
    missing = sorted(codes - {str(r.get("code", "")).zfill(6) for r in buys})
    if missing:
        note += (f" **⚠️ {'、'.join(missing)} 不在本节的真实精选行内**"
                 "(📌保送持仓 / 或 finalists 与决策文件人口不一致)——"
                 "板块分布未计入它,买单只数以决策文件为准。")
    # BLOCKED **结论**归仪表盘③(brief ③ 逐字);这里只在它影响**组合动作**时提一句,
    # 不复述因果,也不再挂「口径:BUY 出自 …」尾注(注文集中 appendix F)。
    if decision.get("blocked"):
        note += " 当日无合格买单 → 不开新仓。"
    return note


def _overlay_band(scan_dir: Path) -> str:
    """仓位 overlay 的前半段(regime 档位 + 菜单病),**与买单数无关**。缺 regime → ""。"""
    try:
        meta = _load_json(scan_dir / "meta.json")
        regime = meta.get("regime")
    except Exception:  # noqa: BLE001
        return ""
    band = {"risk_off": "0–2 成", "range": "3–5 成", "trend": "5–8 成"}.get(regime or "")
    if not band:
        return ""
    sick = ""
    try:
        from autoresearch.scan.menu import l4_budget
        n, _why = l4_budget(scan_dir)
        if n < 30:
            sick = "(菜单病 → 取区间下沿)"
    except Exception:  # noqa: BLE001
        pass
    return f"**仓位建议(overlay,非个股)**:regime={regime} → 总仓位基准 **{band}**{sick};"


def _overlay_tail(n_buys: int) -> str:
    """仓位行的尾巴:**只说动作**,不复述 0买/BLOCKED 的因果(那归 🧭 仪表盘③ 与节 6)。

    2026-08-29 §4.1.1:此前这里写「今日 0 买 → 空仓/底仓与系统读数一致」,同一页仪表盘③
    已经把 0 买结论与机制讲了一遍 —— 同一事实两个展开点,而且措辞还不完全一样。
    """
    return "本次不开新仓。" if n_buys == 0 else f"{n_buys} 只按评级×置信度分配。"


def _position_overlay(scan_dir: Path, rows: list[dict]) -> str:
    """仓位建议(组合 overlay,确定性):regime 档位 + 菜单病取下沿 + 0 买一致性。缺 regime → ""。

    只作用于总仓位,不改单票评级(与策略师"方向只进 L5"同一铁律)。
    **legacy 口径**(0买判断数 ≥OW);active 期改由 `_position_overlay_active` 收尾注入。
    """
    head = _overlay_band(scan_dir)
    if not head:
        return ""
    n_buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    return head + _overlay_tail(n_buys)


def _position_overlay_active(scan_dir: Path, decision: dict | None) -> str:
    """active 口径:0买判断的 n_buys = 决策文件 `buys[]` 的只数。"""
    head = _overlay_band(scan_dir)
    if not isinstance(decision, dict):
        return (head + _DECISION_UNAVAILABLE) if head else _DECISION_UNAVAILABLE
    if not head:
        return ""
    if decision.get("blocked"):
        # 事实归属(§4.1.1):**BLOCKED 结论与它的因果归 🧭 仪表盘③**(brief ③ 逐字),
        # 这里只出「所以要做什么」。此前同一页把「硬资格否决,非择时空仓」讲两遍,
        # 措辞还不完全一样 —— 同一事实两个展开点正是本波要治的病。
        return head + "本次不开新仓。"
    return head + _overlay_tail(len(_decision_buy_codes(decision)))

def _conflict_block(conflicts: dict[str, dict]) -> str:
    """⚖️ 两尺分歧框(Wave9 A-2)——presence-gated,无冲突返回空串。

    呈现契约,**不是**合并规则:确定性价格线与 LLM 基本面终评测的不是同一件事,
    系统把两边的判据、来源、失效条件并排摆出来,由人裁。

    **同票多条价格线全部呈现**(复核 Minor→must-fix,2026-07-30):优先读 `all_hits`
    (`tripwire_conflicts` 产出的结构化列表);缺该键(如手工构造的旧形状 dict——既有
    单测 `test_conflict_block_renders_two_rulers` 就是这么构造的,兼容不改)则把单值
    `tripwire_detail` 当唯一一条兜底。同一票多条命中在**同一单元格内**用「；」全部列出
    ——不挑一条藏一条,这个框存在的意义就是把材料摆给人裁。

    **线出自哪一天**(Wave9 final-fix I-2):`tripwire_conflicts` 现在比对的是**严格
    早于今日**的最新卡(不再是今日自己刚写的卡,见该函数 docstring),单元格附带
    "(线出自 YYYY-MM-DD 卡)" 标注,不让读者误以为这是今天写的线;`all_hits` 缺
    `card_date`(旧形状兜底)时不加标注,逐字节兼容既有单测。
    """
    if not conflicts:
        return ""
    lines = ["", "### ⚖️ 两尺分歧(确定性盯梢线 vs LLM 终评)", "",
             "| 票 | tripwire(价格尺) | LLM 终评(基本面尺) |", "|---|---|---|"]
    for code, c in sorted(conflicts.items()):
        hits = c.get("all_hits") or [{"detail": c.get("tripwire_detail", "")}]
        cell = "；".join(h.get("detail", "") for h in hits if h.get("detail"))
        dates = sorted({h["card_date"] for h in hits if h.get("card_date")})
        if dates:
            cell += f"(线出自 {'/'.join(dates)} 卡)"
        lines.append(f"| {code} | {cell} | **{c['rating']}**"
                     f"(满卡 DD + 双复核折回) |")
    lines += ["", "| | 判据来源 | 失效条件 |", "|---|---|---|",
              "| 价格尺 | 你在**前一交易日(或更早)**卡里写下的盯梢线——非今日新卡,"
              "只看收盘价、不看基本面 | 收盘收复线上 |",
              "| 基本面尺 | 当日满卡尽调 + ≥OW/SELL 双复核中位 | 复核依据的驱动被证伪 |",
              "",
              "**两把尺子测的不是同一件事,系统不合并——人裁。**", ""]
    return "\n".join(lines)


def _pinned_section(scan_dir: Path, analysis_date: str, pinned_rows: list[dict],
                    l1_full: dict, l2_top: dict, ch_map: dict, vmap: dict, n_l1, n_l2,
                    pinned_path: str | Path | None = None,
                    conflicts: dict | None = None) -> str:
    """📌 保送持仓节(design 2026-07-11 §4.1;feedback fb_20260714_001 改结构):保送持仓与真实
    精选**分列**——运行期 `lane=="pinned"` 行(`pinned_rows`,`finalists.csv` 烤入的那次跑的事实)
    渲染成与 §3 buy-list 同结构的完整表 + 「保送理由」列(不占 L3 名额、也不混进 buy-list)。

    **run-time-truth,不再挂 `load_pinned()`**:presence-gate = `pinned_rows` 非空 **或** config
    有 expired。旧设计把 gate 挂在 `load_pinned()` 的 kept 上,`pinned.jsonc` 跑后一改,旧保送票
    就从这份报告凭空消失——而 §3 又已把 `lane==pinned` 剔除,两头都没有 → 保送票蒸发。改挂
    finalists.csv 的 lane 后,报告永远忠实于它自己那次运行。config 只再供 **expired** 尾注
    (过期条目不会出现在 finalists 里,只能从 config 读)。
    """
    expired: list[dict] = []
    try:
        from autoresearch.scan.user_config import load_pinned
        expired = load_pinned(analysis_date, path=pinned_path).get("expired") or []
    except Exception:  # noqa: BLE001 — 可选层,坏 pinned.json 不挡整份报告发布
        expired = []
    if not pinned_rows and not expired:
        return ""
    lines = ["## 📌 保送持仓(手工直通,不占 L3 名额;评级仍按各自 rubric 独立判定)"]
    if pinned_rows:
        # §4.1 节 5:与候选表同一套压缩列(评级/目标/一句依据/L1→L2 + 保送理由),
        # 不再走旧 9 列表 —— 那张表的 `L3精排` 全文列 08-26 实测一格 300+ 字。
        lines += _candidate_table_lines(pinned_rows, l1_full, l2_top, ch_map, vmap, note_col=True)
        lines.append(_conflict_block(conflicts or {}))
    else:
        lines += ["_本次运行内无强留的保送持仓(finalists 无 lane=pinned 行)。_"]
    if expired:
        lines += ["", "_已过期(不再强留,续期请更新 pinned.jsonc):_"]
        for e in expired:
            note = f" ——{e['note']}" if e.get("note") else ""
            lines.append(f"- {e['code']}{note}(已于 {e.get('expires', '—')} 过期)")
    return "\n".join(lines)

#: summary 总字节回归锁 —— **单一事实源已移到 `report_model`**(§6.10 四个预算常量)。
#:
#: 本名字保留为 `SUMMARY_WARN_BYTES` 的兼容别名(顶部 import 带进来),**不在这里再赋一次值**:
#: 08-29 之前它在这里被重新赋成 38KB,把 import 进来的 16KB 覆盖掉 —— 同名常量两个值,
#: `from report_sections import SUMMARY_MAX_BYTES` 会拿到一个作废了的门槛(配置双事实源,
#: 本仓 08-11 裁定过「参数只能有一个事实源」)。
#: 语义不变:它**不是运行期截断阈值**(报告不许因超预算丢内容),只是回归断言的基准。

def _same_chain_block(rows) -> str:
    """同申万一级 ≥2 只 finalist → 并排一行(择链上最佳表达,同链多买=1 个 bet)。<2 → ''。"""
    by_sec: dict[str, list[dict]] = {}
    for r in rows:
        sec = str(r.get("sector") or r.get("industry") or "").strip()
        if sec and sec != "nan":
            by_sec.setdefault(sec, []).append(r)
    multi = {s: rs for s, rs in by_sec.items() if len(rs) >= 2}
    if not multi:
        return ""
    lines = ["#### 🔗 同链对比(同申万一级 ≥2 只 → 择链上最佳表达,同链多买=1 个 bet)",
             "| 行业 | 同链 finalists(评级 · 目标) |", "|---|---|"]
    for sec in sorted(multi, key=lambda s: -len(multi[s])):
        cell = "、".join(f"{r.get('name', '')}(**{r.get('rating', '—')}** · {r.get('target', '—')})"
                         for r in sorted(multi[sec], key=_sortkey))
        lines.append(f"| {sec}({len(multi[sec])}只) | {cell} |")
    return "\n".join(lines)

def _load_market_view(scan_dir: Path) -> str:
    """读 L2 后策略师写的 market_view.md staging(缺 → '')。assemble 仍零-LLM(只读文件)。

    嵌入前剥样板:自带 H1(报告已有 H1,双标题是噪声)+ 免责节(报告已有诚实局限)。"""
    p = scan_dir / "market_view.md"
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and "免责" in ln:
            lines = lines[:i]
            break
    return "\n".join(lines).strip()

def regime_and_drift(scan_dir: Path) -> tuple[str, str]:
    """从 L1 帧算今日 regime + 与 weights.meta.regime_calib 比对 → (summary 行, drift reason|'')。

    给 summary 一句 regime 定性(哑铃/落刀的关键上下文)+ 把 drift 喂 self_review warn。
    缺 L1/regime 依赖 → ('', '')(老路不破)。
    """
    try:
        import pandas as pd

        from autoresearch.common.regime import classify_regime, detect_drift
        from autoresearch.common.scoring import _load_weights
    except Exception:  # noqa: BLE001
        return "", ""
    src = scan_dir / "L1_scored_full.csv"
    if not src.exists():
        src = scan_dir / "L1_recall_top1000.csv"
    if not src.exists():
        return "", ""
    try:
        df = pd.read_csv(src)
    except Exception:  # noqa: BLE001
        return "", ""
    st = classify_regime(df)
    drifted, reason = detect_drift(st, _load_weights().get("meta", {}))
    zh = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}.get(st.label, st.label)
    line = (f"**市场 regime**:{zh}(breadth {st.breadth:.0%}·中位动量 {st.med_mom:+.1f}%"
            + (f";⚠️ {reason}" if drifted else "") + ")")
    return line, (reason if drifted else "")

def _degraded_line(scan_dir: Path) -> str:
    """B 级数据降级一行(`degraded.json`,由 `universe.run` 落)。presence-gated:无文件 → ""。

    A 级(地基)违约在取数处就抛异常阻断了,报告压根不会生成;所以这里显示的一定是 B 级增强端点
    缺失(北向/质押/席位/公告…)。**降级必须可见**:否则读报告的人无从分辨某面旗是"真没有"
    还是"没取到"(design 2026-07-12-data-contracts-design.md §1:系统有降级能力,曾经没有
    「我降级了」的传达能力)。
    """
    p = Path(scan_dir) / "degraded.json"
    if not p.exists():
        return ""
    try:
        recs = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not recs:
        return ""
    from autoresearch.data.contracts import render

    return render(recs)

def _review_ctx(scan_dir: Path, rows: list[dict], regime_drift: str = "") -> dict:
    """self_review 的**不纯**输入(读 L1 csv / verify / market_view 存在性)→ 冻进 ReportModel。

    `summary_text` 不在这里 —— 它要等 `render_summary` 出正文才有,由 `review_result()` 补。
    B+ 拆分后这是 prepare 阶段唯一一次读这些盘;渲染层不再回头读。
    """
    l1 = {}
    if (scan_dir / "L1_scored_full.csv").exists():
        l1 = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    finals = []
    for r in rows:
        lf = l1.get(str(r.get("code", "")).zfill(6), {})
        finals.append({"code": str(r.get("code", "")).zfill(6), "rating": r.get("rating"),
                       "sector": r.get("sector") or r.get("industry"),
                       "composite": lf.get("composite"), "winner_rate": lf.get("winner_rate"),
                       "pct_60d": lf.get("pct_60d"), "rsi6": lf.get("rsi6"),
                       "main_net_ratio": lf.get("main_net_ratio"),
                       "rubric_suggest": r.get("rubric_suggest"), "rubric_dev": r.get("rubric_dev")})
    n_present = sum(1 for r in rows
                    if r.get("target") not in ("⚠️卡片缺失", BLIND_CARD_TARGET))
    # E3b(task-2.4)· `flow.buys_n` 的口径:
    # shadow 期 = ≥OW 张数(现行为,逐字不变);active 期这个数**不再是买单数** ——
    # 买单只存在于 `_relative_buy_decision.json`,而本函数跑在 `build_summary` 内部,比
    # writer-1 早一站,盘上那份多半还是前一日的。所以 active 期 `buys_n=None` +
    # `buys_n_source` 显式标记「本刻不可知」,**不拿 ≥OW 张数冒充**。
    # ⚠️ 唯一消费者是下方 self_review.review 里那条**已注释停用**的「买单未过 skeptic」lint
    # (`self_review.py:169-171`);将来恢复它时必须先解决"这个数在本刻不可知"这件事
    # (要么把那条 lint 移到 brief_lint 那一层,要么读收尾注入后的决策文件),不能直接
    # 把 buys_n 当买单数用。
    buys_n = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    buys_src = "rating≥OW(legacy 绝对门)"
    if is_active():
        buys_n, buys_src = None, "deferred:决策文件在 build_summary 之后才写,本刻不可知"
    return {"finalists": finals, "n_cards_expected": len(rows), "n_cards_present": n_present,
            "regime_drift": regime_drift,
            "flow": {                                      # 编排完备性 lint(LLM 段可能被静默跳过)
                "buys_n": buys_n,
                "buys_n_source": buys_src,
                "verify_n": len(_load_verify(scan_dir)),
                "has_market_view": (scan_dir / "market_view.md").exists(),
                "finalists_n": len(rows)}}


def _self_review_banner(scan_dir: Path, rows: list[dict], summary_text: str,
                        regime_drift: str = "") -> str:
    """发布前机械自检(self_review 硬门)→ 报告顶部 banner。缺依赖/无问题 → 空串(老路不破)。

    **兼容路径**:B+ 拆分后生产走 `prepare_report_model`(冻 ctx)→ `review_result`(纯)
    → `finalize_review_artifacts`(落 gate_fires)。本函数保留给旧调用方与旧测试,
    行为逐字节不变(ctx 构造已抽成 `_review_ctx`,附加 lint 抽成 `_review_extras`)。
    """
    try:
        import autoresearch.scan.self_review as self_review
    except Exception:  # noqa: BLE001
        return ""
    ctx = dict(_review_ctx(scan_dir, rows, regime_drift))
    ctx["summary_text"] = summary_text
    import contextlib
    res = self_review.review(ctx)
    with contextlib.suppress(Exception):                            # 卡片契约 lint(在 dump 前合并 → 留痕)
        extra = self_review.card_contract_lint(scan_dir)
        if extra:
            res["failures"].extend(extra)
            res["n_warn"] = res.get("n_warn", 0) + len(extra)
    with contextlib.suppress(Exception):                            # intel as-of 前视机检(advisory)
        intel_extra = self_review.intel_future_dates_lint(scan_dir, scan_dir.name)
        if intel_extra:
            res["failures"].extend(intel_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(intel_extra)
    with contextlib.suppress(Exception):                            # intel 时效三窗机检(Wave7 批 N,advisory)
        recency_extra = self_review.intel_recency_lint(scan_dir, scan_dir.name)
        if recency_extra:
            res["failures"].extend(recency_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(recency_extra)
    with contextlib.suppress(Exception):                            # 产物形状 lint(线C 2026-07-18;全 warn/info 起步)
        shape_extra = self_review.product_shape_lint(scan_dir, scan_dir.name)
        if shape_extra:
            res["failures"].extend(shape_extra)
            res["n_warn"] = res.get("n_warn", 0) + sum(1 for x in shape_extra if x.get("severity") == "warn")
    with contextlib.suppress(Exception):                            # 通道活性探针(2026-08-21 低位转强波 §5.3,恒 warn)
        live_extra = self_review.channel_liveness_lint(scan_dir, scan_dir.name)
        if live_extra:
            res["failures"].extend(live_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(live_extra)
    with contextlib.suppress(Exception):                            # 配置生效对账(Wave11 B4,usage_reconcile)
        # ledger_path 显式算成 scan_dir 的兄弟目录(scan_dir.parent.parent / learning / ...),
        # 不依赖 usage_reconcile_lint 的 cwd 相对缺省——scan_dir 在生产里本来就是
        # `context/scan/<date>`(见上方 802 行前后 `scan_dir == Path("context/scan") / date`
        # 的同款假设),这里算出来的路径与缺省值 byte-identical,但让本函数可以脱离 cwd
        # 被 tmp_path 测试摆布,不用靠 monkeypatch.chdir。
        usage_ledger = scan_dir.parent.parent / "learning" / "usage_reconcile.jsonl"
        usage_extra = self_review.usage_reconcile_lint(scan_dir.parent, ledger_path=usage_ledger)
        if usage_extra:
            res["failures"].extend(usage_extra)
            res["n_warn"] = res.get("n_warn", 0) + sum(1 for x in usage_extra if x.get("severity") == "warn")
            n_fail_extra = sum(1 for x in usage_extra if x.get("severity") == "fail")
            if n_fail_extra:               # 前 4 个 extra lint 只返回 warn/info,从不用管这两行;
                res["n_fail"] = res.get("n_fail", 0) + n_fail_extra   # usage_reconcile_lint 是唯一
                res["ok"] = False                                     # 会升 fail 的,补上防 banner 头失真
    with contextlib.suppress(Exception):
        self_review.dump_gate_fires(scan_dir, res, scan_dir.name)   # R3 留痕;IO 失败不阻发布
    return self_review.render_banner(res)

def _buylist_table_lines(rows: list[dict], l1_full: dict, l2_top: dict, ch_map: dict,
                         vmap: dict, n_l1, n_l2, *, note_col: bool = False) -> list[str]:
    """逐阶段结论表(§3 buy-list 与 📌 保送持仓共用):header + sep + 每行。

    `note_col=True` 末尾追加「保送理由」列(取 pinned_note)。**genuine 场景(note_col=False)
    输出与历史 §3 渲染逐字节一致**(test_assemble buy-list 契约:L1召回/L2粗排/L3精排/L4研究/
    评级/目标 列 + 可选 🛡️红队 列)。"""
    vcol, vsep = (" 🛡️红队 |", "---|") if vmap else ("", "")
    ncol, nsep = (" 保送理由 |", "---|") if note_col else ("", "")
    lines = [
        f"| # | 名称 | 板块 | L1召回(#/{n_l1}) | L2粗排(#/{n_l2}) | L3精排 | L4研究·结论 | 评级 | 目标(EV) |"
        + vcol + ncol,
        "|---|---|---|---|---|---|---|---|---|" + vsep + nsep]
    for i, r in enumerate(rows, 1):
        code = str(r.get("code", "")).zfill(6)
        vcell = f" {_verify_badge(code, vmap)} |" if vmap else ""
        l3txt = _strip(r.get("thesis") or r.get("triage_reason", ""))
        conv = r.get("conviction")
        l3cell = l3txt + (f"·conv{conv}" if conv else "")
        rating_cell = f"**{r.get('rating', '—')}**" + (" 🎭复核分歧" if r.get("ens_flag") else "")
        ncell = f" {_strip(r.get('pinned_note', '') or '—')} |" if note_col else ""
        lines.append(
            f"| {i} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| {_l1_cell(code, l1_full, ch_map)} | {_l2_cell(code, l2_top)} | {l3cell} "
            f"| {_strip(r.get('l4', '—'))} "
            f"| {rating_cell} | {r.get('target', '—')} |" + vcell + ncell)
    return lines

# ══════════════════════════════════════════════════════════════════════════════
# B+ 三段式:prepare_report_model(读盘 + finalize 副作用)→ render_summary / render_appendix(纯)
# design: docs/specs/2026-08-28-summary-slimdown-design.md §4.1.2 / §6.1
#
# 为什么要切:旧 build_summary 一个函数同时读盘 / fold 评级 / 落盘 / 渲染,于是「同一事实
# 被多个渲染入口各自重读重算」成了结构性风险(08-26 实测 regime 印 3 次、门柱两个口径同屏
# 打架)。切开之后**业务计算与 writer 时序一个字没动** —— 决策 finalize 与落盘仍按原顺序在
# prepare 内完成,渲染层拿到的是冻结事实。
# ══════════════════════════════════════════════════════════════════════════════

#: 策略师 market_view.md 的小节头(`1. **一句话定调**:…`)。六小节结构是 macro-brief 的
#: 机器契约(agent def + playbook 双锁),这里只按它切片,**不改 agent**。
_MV_HEAD_RE = re.compile(r"^[ \t]*([1-6])[.、]\s*\*\*", re.M)

#: 进 summary「市场地形」的小节:2 市场结构 / 3 板块红黑榜 / 5 关注。
#: 1 定调 → 仪表盘①独占(事实归属);4 操作基调 → 节 3「行动」独占;6 免责 → appendix G。
_MV_SUMMARY_SECTIONS = ("2", "3", "5")


def slice_market_view(text: str) -> tuple[dict[str, str], bool]:
    """策略师稿按六小节切片 → ({"1": 全文, "2": …}, parse_ok)。

    `parse_ok=False` = 标题缺失 / 格式被污染,**不做整段回退**(§6.8):summary 只印一行
    链接 + 落 warn,全文照常进 appendix C。整段回退曾把 2.6KB 策略师散文连同「操作基调」
    一起塞进决策层,正是本波要治的重复展开。
    """
    if not text.strip():
        return {}, False
    hits = list(_MV_HEAD_RE.finditer(text))
    if not hits:
        return {}, False
    out: dict[str, str] = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        seg = text[m.start():end]
        # 第 6 小节「仅供研究,非投资建议」不带 `**`,匹配不到头 → 会被上一节吞掉。
        # 显式在裸编号行处收尾(它是模板尾注,归 appendix G,决策层页脚已有一份)。
        tail = re.search(r"^[ \t]*[6-9][.、]\s*(?!\*\*)", seg[1:], re.M)
        if tail:
            seg = seg[:tail.start() + 1]
        out[m.group(1)] = seg.strip()
    ok = all(k in out for k in _MV_SUMMARY_SECTIONS)
    return out, ok


def _l1l2_cell(code: str, l1_full: dict, l2_top: dict, ch_map: dict) -> str:
    """候选表的 `L1→L2` 合并列:`#3632·healthy/主力 → #136`。

    gbdt 分去掉 —— 它是**遗留列名**(值 = sn_composite 分层采样序,不是模型分),
    在决策层每票占 8 个字符却回答不了任何决策问题;口径见 appendix F「漏斗口径」。
    """
    l1 = _l1_cell(code, l1_full, ch_map)
    l2 = _l2_cell(code, l2_top).split("·g")[0]
    if l1 == "—" and l2 == "—":
        return "—"
    return f"{l1} → {l2}"


def _evidence_of(r: dict) -> str:
    """候选表「一句依据」单元格(prepare 阶段已算好并挂在行上)。"""
    ev = r.get("_evidence") or {}
    return _strip(str(ev.get("text") or "—")) or "—"


def _candidate_table_lines(rows: list[dict], l1_full: dict, l2_top: dict, ch_map: dict,
                           vmap: dict, *, note_col: bool = False) -> list[str]:
    """候选表 / 📌 保送表(§4.1 节 4/5 共用)。

    与旧 §3 表的差别:删 `L3精排` 全文列(中位 292 字一格 → appendix C)、`L1召回`/`L2粗排`
    合并成一列、🛡️红队 与 🎭 徽章并进评级格 —— 自由文本只剩「一句依据」一列。
    """
    ncol, nsep = (" 保送理由 |", "---|") if note_col else ("", "")
    lines = ["| # | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2 |" + ncol,
             "|---|---|---|---|---|---|---|" + nsep]
    for i, r in enumerate(rows, 1):
        code = str(r.get("code", "")).zfill(6)
        badges = ""
        if vmap and _verify_badge(code, vmap):
            badges += f" {_verify_badge(code, vmap)}"
        if r.get("ens_flag"):
            badges += " 🎭复核分歧"        # 保留全字:光一个 🎭 读者不知道它在说什么
        ncell = f" {_strip(r.get('pinned_note', '') or '—')} |" if note_col else ""
        lines.append(
            f"| {i} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| **{r.get('rating', '—')}**{badges} | {r.get('target', '—')} "
            f"| {_evidence_of(r)} | {_l1l2_cell(code, l1_full, l2_top, ch_map)} |" + ncell)
    return lines


def _buy_constraint(scan_dir: Path, genuine_rows: list[dict]) -> tuple[str, list[str]]:
    """节 6「为什么没有 BUY / BUY 资格与约束」→ (标题, 行)。**单源** `decision_records.json`。

    只出**统计**(早停分桶 + 三门 ✗ 计数),**不出 BUY/BLOCKED 结论** —— 结论归 🧭 仪表盘③
    (事实归属表)。也**不读** `_relative_buy_decision.json`:那份文件由 writer-1 在本函数
    之后才写,就地读会读到上一日那份(2026-08-19 取证:8 份 brief 里 4 份不一致的真身)。

    `gate_status` 解析卡片自由文本的那份直方图**不在这里** —— 它口径不同(分母=可解析卡)
    且有漏读加粗 `**✗**` 的前科,整块下沉 appendix D,两个数不同屏就不会打架。
    """
    rec = _load_json(Path(scan_dir) / "decision_records.json")
    records = rec.get("records") if isinstance(rec, dict) else None
    if not isinstance(records, list):
        records = []
    # 人口口径 = **全部卡**(含 📌 保送),与 brief ③ / 🧭 仪表盘③ 逐字同源。
    # 08-26 实测:按「只数 genuine」会得到「满卡 3 张」,而同一页仪表盘印「4 张」——
    # 同屏两个生产者打架正是本波要根治的病(门柱两口径同族疤),宁可口径宽也要同源。
    # 「满卡」= 没早停的卡(不是「gate_states 有非 UNKNOWN 的卡」):早停卡按定义不写
    # 三门段,两类不混算;满卡里门柱仍可能 UNKNOWN(卡片没写全),那是 0 计不是不计。
    stops = Counter()
    gate_fail = Counter()
    n_full = 0
    for r in records:
        es = r.get("early_stop") or {}
        if es.get("reason"):
            stops[str(es["reason"])] += 1
            continue
        n_full += 1
        gs = r.get("gate_states") or {}
        for g in _GATES3:
            if gs.get(g) == "FAIL":
                gate_fail[g] += 1
    has_buy = any(r.get("rating") in ("Buy", "Overweight") for r in genuine_rows)
    title = "## BUY 资格与约束" if has_buy else "## 为什么没有 BUY"
    lines: list[str] = []
    if stops:
        detail = " · ".join(f"{k} {v}" for k, v in stops.most_common())
        lines.append(f"早停 {sum(stops.values())} 卡:{detail}")
    if n_full:
        detail = " · ".join(f"{g} {gate_fail.get(g, 0)}" for g in _GATES3)
        lines.append(f"满卡 {n_full} 张三门 ✗:{detail} {method_link('门柱口径', 'gate')}")
    return title, lines


def _review_extras(scan_dir: Path) -> list[dict]:
    """六条**不纯**附加 lint(读 details/ 与 staging)→ prepare 阶段跑一次,冻进 review_ctx。

    渲染层只做 `self_review.review(ctx)` 这一步纯计算,不再回头读盘。
    """
    import contextlib

    import autoresearch.scan.self_review as self_review
    extras: list[dict] = []
    probes = (
        lambda: self_review.card_contract_lint(scan_dir),
        lambda: self_review.intel_future_dates_lint(scan_dir, scan_dir.name),
        lambda: self_review.intel_recency_lint(scan_dir, scan_dir.name),
        lambda: self_review.product_shape_lint(scan_dir, scan_dir.name),
        lambda: self_review.channel_liveness_lint(scan_dir, scan_dir.name),
        lambda: self_review.usage_reconcile_lint(
            scan_dir.parent, ledger_path=scan_dir.parent.parent / "learning" / "usage_reconcile.jsonl"),
    )
    for probe in probes:
        with contextlib.suppress(Exception):
            got = probe()
            if got:
                extras.extend(got)
    return extras


def review_result(model: ReportModel, summary_text: str) -> dict:
    """对**最终 summary 正文**跑 self_review(纯:只用冻结的 ctx + 传入的正文)。"""
    try:
        import autoresearch.scan.self_review as self_review
    except Exception:  # noqa: BLE001
        return {}
    ctx = dict(model.review_ctx)
    extras = ctx.pop("_extras", []) or []
    ctx["summary_text"] = summary_text
    try:
        res = self_review.review(ctx)
    except Exception:  # noqa: BLE001
        return {}
    if extras:
        res.setdefault("failures", []).extend(extras)
        res["n_warn"] = res.get("n_warn", 0) + sum(1 for x in extras if x.get("severity") != "fail")
        n_fail_extra = sum(1 for x in extras if x.get("severity") == "fail")
        if n_fail_extra:
            res["n_fail"] = res.get("n_fail", 0) + n_fail_extra
            res["ok"] = False
    return res


def _render_banner(res: dict, *, aggregate: bool) -> str:
    """banner 渲染(A1 给 `render_banner` 加了 `aggregate=`;未落地时回退旧签名)。"""
    if not res:
        return ""
    try:
        import autoresearch.scan.self_review as self_review
    except Exception:  # noqa: BLE001
        return ""
    try:
        return self_review.render_banner(res, aggregate=aggregate)
    except TypeError:                     # A1 尚未落地 → 老签名,逐字节旧行为
        return self_review.render_banner(res)


def attach_review(model: ReportModel, summary_text: str) -> ReportModel:
    """把「对最终正文跑完的 review」回填进模型(纯;frozen dataclass 走 replace)。

    appendix A 要的是**非聚合**全文 banner,而 summary 顶部用聚合版 —— 同一份 `res`
    两种渲染,保证两处说的是同一件事。
    """
    res = review_result(model, summary_text)
    return dataclasses.replace(model, review_result=res,
                               banner_full=_render_banner(res, aggregate=False))


def finalize_review_artifacts(scan_dir: Path | str, model: ReportModel) -> dict:
    """**不纯**:把 review 结果落 `gate_fires.csv`(GATE4 的判据源)。

    旧路径里这个副作用藏在 `_self_review_banner` 内部;prepare/render 切开后它必须由
    publisher 显式调用 —— 漏了它 GATE4 就没有输入(判据 = `gate_fires.csv` 里有无
    `severity=fail` 行),而 `brief_lint` 的结果也追加在同一份文件里。
    """
    import contextlib
    res = model.review_result or {}
    if not res:
        return {}
    with contextlib.suppress(Exception):
        import autoresearch.scan.self_review as self_review
        self_review.dump_gate_fires(Path(scan_dir), res, Path(scan_dir).name)
    return res


def _budget_warn(text: str, warn_bytes: int, target_bytes: int, label: str) -> str | None:
    """展示层字节预算(§6.10):超 warn 上限返回一行文案,**不截断、不改评级、不毙 GATE4**。"""
    n = len(text.encode("utf-8"))
    if n <= warn_bytes:
        return None
    return (f"{label} {n:,}B 超展示预算(目标 {target_bytes:,}B / warn {warn_bytes:,}B)"
            f" —— 排版问题,不影响结论")


def summary_budget_warn(text: str) -> str | None:
    """最终注入态 summary 的预算 warn(post_run 刷完两块之后调)。"""
    return _budget_warn(text, SUMMARY_WARN_BYTES, SUMMARY_TARGET_BYTES, "summary.md")


def prepare_report_model(scan_dir: Path, analysis_date: str, hhmm: str, folder: str,
                         pinned_path: str | Path | None = None) -> ReportModel:
    """读盘 → 评级 fold → 决策落盘 → 整形,**只跑一次**,产出不可变 `ReportModel`。

    这一段的顺序**逐字沿用旧 `build_summary`**(verify 折回 → ensemble fold → 落
    `_final_ratings.json` / `decision_records.json` / dissent → 排序 → 分列),
    因为下游 finalizer 依赖它们的先后(relative_buy.py I-1 的护照顺序坑)。
    本波不借重构改任何业务计算。
    """
    scan_dir = Path(scan_dir)
    meta = _load_json(scan_dir / "meta.json")
    recall = _read_csv(scan_dir / "L1_recall_top1000.csv")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    l1_full = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    l2_top = {str(r.get("code", "")).zfill(6): r for r in keep}
    rows = [_finalist_row(scan_dir, fr) for fr in finals]
    from autoresearch.scan.decision_finalize import mark_blind_cards
    mark_blind_cards(scan_dir, rows)          # 盲卡先标,再走 verify/ensemble/终评级落盘
    for r in rows:
        r["_source_rating"] = r.get("rating", "—")
    vmap = _load_verify(scan_dir)
    for r in rows:
        v = vmap.get(str(r.get("code", "")).zfill(6))
        if v and v["verdict"] in ("降级", "否决"):
            r["rating"] = _apply_verify_downgrade(r.get("rating", "Hold"), v["verdict"])
            r["proposal"] = _PROPOSAL_BY_RATING.get(r["rating"], r.get("proposal", "—"))
    for r in rows:
        r["_post_verify_rating"] = r.get("rating", "—")
    emap = _load_ensemble(scan_dir)
    for r in rows:
        e = emap.get(str(r.get("code", "")).zfill(6))
        r["_ensemble_ratings"] = list((e or {}).get("ratings") or [])
        if not e:
            continue
        folded = _apply_ensemble_fold(r.get("rating", "Hold"), e)
        if folded != r.get("rating"):
            r["rating"] = folded
            r["proposal"] = _PROPOSAL_BY_RATING.get(folded, r.get("proposal", "—"))
        if _ensemble_flag(e):
            r["ens_flag"] = True
    _dump_final_ratings(scan_dir, rows)
    _dump_decision_records(scan_dir, rows, vmap, emap)
    dump_dissent_records(scan_dir, build_dissent_records(rows, emap))
    rows.sort(key=_sortkey)
    pinned_rows = [r for r in rows if str(r.get("lane", "")).strip() == "pinned"]
    genuine_rows = [r for r in rows if str(r.get("lane", "")).strip() != "pinned"]
    n_l1 = meta.get("after_gate_a") or meta.get("universe") or len(l1_full) or "?"
    n_l2 = meta.get("l2_n") or len(l2_top) or "?"
    ch_map = {c: (r.get("recall_channels") or "") for c, r in l2_top.items()}
    regime_line, regime_drift = regime_and_drift(scan_dir)

    # ── 一句依据(§6.7):终评级同向,反向段绝不补空 ──────────────────────
    for r in rows:
        code = str(r.get("code", "")).zfill(6)
        text = _decision_text(scan_dir, code) or ""
        try:
            from autoresearch.scan.l4.parsers import pick_rating_aligned_evidence
            r["_evidence"] = pick_rating_aligned_evidence(text, r.get("rating", ""), r)
        except ImportError:               # A1 未落地 → 退回旧 `l4` 一句(标明来源以便对账)
            r["_evidence"] = {"text": _strip(r.get("l4", "") or "—"),
                              "source": "legacy_l4", "polarity": "unknown"}

    import contextlib as _ctx

    # ── 各生产者直出的块(presence-gated;渲染层只决定它们落 summary 还是 appendix)──
    from autoresearch.scan.market import (
        market_pack,
        render_fallback_pulse,
        render_funnel_readout,
        render_sector_top3,
        render_temperature_line,
    )
    temp_line = render_temperature_line(analysis_date)
    from autoresearch.scan.calendar import calendar_section
    calendar_block = calendar_section(scan_dir) or ""
    # minor-7(final whole-branch review):唯一允许 I/O 的 prepare 阶段读一次旋钮,原样带给
    # 纯函数 render_summary(该函数明确"不读盘"),让发布出去的标题只在旋钮真的开着时才带
    # "调样=被动调仓收盘日"这个从句——旋钮关时逐字保持波前的两句式标题(parity)。
    from autoresearch.scan.user_config import knob
    index_rebalance_enabled = bool(knob("calendar", "index_rebalance", None, False))
    active = is_active()
    portfolio_block = portfolio_placeholder() if active else _portfolio_note(genuine_rows)
    overlay_block = ((overlay_placeholder() if _overlay_band(scan_dir) else "") if active
                     else _position_overlay(scan_dir, genuine_rows))
    run_mode_banner = ""
    with _ctx.suppress(Exception):
        from autoresearch.scan.run_mode import load as _load_run_mode
        _mode = _load_run_mode(scan_dir)
        if _mode is not None:
            run_mode_banner = _mode.banner() or ""
    from autoresearch.scan.decision_finalize import tripwire_conflicts
    conflicts = tripwire_conflicts(scan_dir, analysis_date, pinned_rows)
    with _ctx.suppress(Exception):
        (scan_dir / "_tripwire_conflicts.json").write_text(
            json.dumps(conflicts, ensure_ascii=False), encoding="utf-8")
    pinned_section = _pinned_section(scan_dir, analysis_date, pinned_rows, l1_full, l2_top,
                                     ch_map, vmap, n_l1, n_l2, pinned_path=pinned_path,
                                     conflicts=conflicts) or ""
    mv_raw = _load_market_view(scan_dir)
    mv_slices, mv_ok = slice_market_view(mv_raw)
    fallback_pulse = ""
    if not mv_raw:
        with _ctx.suppress(Exception):
            fallback_pulse = render_fallback_pulse(market_pack(scan_dir)) or ""
    sector_top3 = ""
    with _ctx.suppress(Exception):
        sector_top3 = render_sector_top3(market_pack(scan_dir)) or ""
    stage_overview = (_stage_overview("召回(L1)", recall,
                                      "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
                      + _stage_overview("粗排(L2)", keep,
                                        f"确定性分层采样({meta.get('l2_engine', 'stratified')});"
                                        "sn_composite 排序+风格桶 floor+sector cap,零模型零 LLM。"))
    menu_block = ""
    with _ctx.suppress(Exception):
        from autoresearch.scan.menu import menu_health
        menu_block = menu_health(scan_dir) or ""
    buy_title, buy_lines = _buy_constraint(scan_dir, genuine_rows)

    # ── D-2 预留:隔夜窗海外事件(外源稿;文件不在 → 空,parity 不破)────────
    overseas_lines: list[str] = []
    with _ctx.suppress(Exception):
        from autoresearch.scan.overseas import summary_lines as _overseas_lines
        overseas_lines = _overseas_lines(scan_dir) or []

    # ── self_review 输入冻结(不纯的六条 lint 现在只跑一次)────────────────
    review_ctx = _review_ctx(scan_dir, rows, regime_drift)
    extras = _review_extras(scan_dir)
    # §6.8:策略师稿在场但**分节解析失败** → summary 只印一行链接(不整段回退),
    # 同时必须落一条 warn —— 否则「决策层今天没有市场地形」这件事在报告里没有任何痕迹,
    # 而它恰恰是「产物能证明跑过什么、不能证明没跑过什么」要防的静默降级。
    if mv_raw and not mv_ok:
        extras.append({"check": "产物形状·market_view分节", "severity": "warn",
                       "detail": "market_view.md 在场但六小节标题解析失败 —— "
                                 "summary 只留链接,全文见 appendix C(策略师契约:"
                                 "`1. **一句话定调**` 这样的编号+加粗小节头)"})
    review_ctx["_extras"] = extras

    return ReportModel(
        analysis_date=analysis_date, hhmm=hhmm, folder=folder, meta=meta,
        rows=rows, genuine_rows=genuine_rows, pinned_rows=pinned_rows,
        vmap=vmap, emap=emap, conflicts=conflicts,
        l1_full=l1_full, l2_top=l2_top, ch_map=ch_map, n_l1=n_l1, n_l2=n_l2,
        recall_rows=recall, keep_rows=keep, finals_rows=finals,
        regime_line=regime_line, regime_drift=regime_drift, temp_line=temp_line,
        calendar_block=calendar_block, index_rebalance_enabled=index_rebalance_enabled,
        portfolio_block=portfolio_block,
        overlay_block=overlay_block, run_mode_banner=run_mode_banner,
        same_chain_block=_same_chain_block(genuine_rows),
        ensemble_dissent_lines=_ensemble_dissent_lines(emap, rows) or [],
        pinned_section=pinned_section,
        market_view_raw=mv_raw, market_view_slices=mv_slices, market_view_parse_ok=mv_ok,
        fallback_pulse=fallback_pulse, funnel_readout=render_funnel_readout(scan_dir) or "",
        sector_top3_block=sector_top3,
        funnel_table_lines=_funnel_rows(meta, len(keep) or "?", len(genuine_rows),
                                        len(rows), n_pinned=len(pinned_rows)),
        degraded_line=_degraded_line(scan_dir),
        stage_overview_lines=stage_overview, menu_health_block=menu_block,
        timing_lines=_stage_token_estimate(scan_dir),
        gate_histogram_block=_gate_histogram(scan_dir, genuine_rows),
        verify_detail_lines=_verify_detail(vmap),
        overseas_calendar_lines=overseas_lines,
        identity_line=f"数据截至 {analysis_date} 收盘 · 发布 {hhmm[:2]}:{hhmm[2:]}",
        buy_constraint_lines=buy_lines, buy_constraint_title=buy_title,
        review_ctx=review_ctx,
    )


def render_summary(model: ReportModel) -> str:
    """**纯函数**:ReportModel → 决策层 `summary.md`(11 节,节序见模块头)。

    不读盘、不写盘、不 fold 评级。缺料的节 presence-gated 直接不出现 —— 决策层允许
    「这节今天没有」,现场层(appendix)才要求骨架恒在。
    """
    out: list[str] = []

    # 1 身份(结论不在这里 —— 结论归仪表盘)
    out += [f"# A股扫描 · {model.analysis_date}(run `{model.folder}`)", model.identity_line, ""]

    # 2 🧭 决策仪表盘(managed;brief ①②③④ 逐字注入)
    out += [dashboard_placeholder(), ""]

    # 3 行动
    act: list[str] = []
    if model.overlay_block:
        act.append(model.overlay_block)
    if model.portfolio_block:
        act.append(model.portfolio_block)
    chain = _same_chain_line(model.genuine_rows)
    if chain:
        act.append(chain)
    act += [ln for ln in model.ensemble_dissent_lines if ln]
    if model.run_mode_banner:
        act.append(model.run_mode_banner)
    if act:
        out += ["## 行动", *act, ""]

    # 4 候选
    if model.genuine_rows:
        out += [f"## 候选({len(model.genuine_rows)} 只)"]
        out += _candidate_table_lines(model.genuine_rows, model.l1_full, model.l2_top,
                                      model.ch_map, model.vmap)
        out += [f"_{method_link('一句依据', 'evidence')} · 论点全文见 "
                f"{appendix_link('研究附录', 'research')} 与 `details/`_", ""]

    # 5 📌 保送持仓
    if model.pinned_section:
        out += [model.pinned_section, ""]

    # 6 为什么没有 BUY / BUY 资格与约束(单源;结论归仪表盘③)
    if model.buy_constraint_lines:
        out += [model.buy_constraint_title, *model.buy_constraint_lines, ""]

    # 7 市场地形(策略师 2/3/5;全文 → appendix C)
    if model.market_view_raw:
        if model.market_view_parse_ok:
            picked = [model.market_view_slices[k] for k in _MV_SUMMARY_SECTIONS
                      if model.market_view_slices.get(k)]
            out += ["## 市场地形(首席策略师 · 描述性)", *picked, ""]
        else:
            out += ["## 市场地形(首席策略师 · 描述性)",
                    f"_策略师分节解析失败,全文见 {appendix_link('研究附录', 'research')}_", ""]
    elif model.fallback_pulse:
        out += ["## 市场地形(确定性脉搏)", model.fallback_pulse, ""]

    # 8 行业 top3
    if model.sector_top3_block:
        out += [_sector_top3_compact(model.sector_top3_block), ""]

    # 9 📅 未来 14 天(生产者自带 `### 📅 …` 标题 → 剥掉,避免与本节标题重复)
    cal_body = "\n".join(ln for ln in model.calendar_block.splitlines()
                         if not ln.lstrip().startswith("#")).strip()
    cal_lines = [ln for ln in (cal_body, *model.overseas_calendar_lines) if ln]
    if cal_lines:
        # fix(task-12 两处 stale claim 之一):标题曾漏「调样」——calendar.py 自己生成的
        # `### 📅 …` 内标题早已是三项(披露=催化锚,解禁=风险窗,调样=被动调仓收盘日),
        # 但内标题被上面 `cal_body` 剥掉、本节标题是这里另起的一份硬编码拷贝,没跟着改。
        # 正文行本身(cal_lines)一直都渲染到位——这只是标题漏字,不是数据没接进来。
        # minor-7(final whole-branch review):那次修复把三项标题**无条件**焊死——旋钮关时
        # (`calendar.index_rebalance=false`,单杆回滚)日历第三腿从未产过一行,`cal_lines`
        # 却可能仍非空(披露/解禁两腿与该旋钮无关),标题继续断言"调样=被动调仓收盘日"就是这个
        # 分支**唯一**的旋钮关偏离(除版本字符串外),parity 承诺("旋钮关 = 逐字不变")因此不成立。
        # `model.index_rebalance_enabled` 在 prepare 阶段读一次旋钮、原样带过来——本函数是纯
        # 函数,不读盘,只按这个位挑两句式还是三句式标题。
        heading = ("## 📅 未来 14 天(披露=催化锚,解禁=风险窗,调样=被动调仓收盘日;事实日期非方向)"
                  if model.index_rebalance_enabled else
                  "## 📅 未来 14 天(披露=催化锚,解禁=风险窗;事实日期非方向)")
        out += [heading, *cal_lines, ""]

    # 10 运行事实(managed 紧凑一行;完整块 → appendix E)
    start, end = RUN_OBSERVATION_MARKERS
    out += ["## 运行事实",
            f"{start}_运行观测由 post_run 注入;此处为占位——看到本行说明注入未跑,"
            f"读 {appendix_link('运行明细', 'runtime')}。_{end}", ""]

    # 11 诚实局限(锚字面勿动:💸 注入的回退锚)
    out += ["## 诚实局限",
            f"召回/粗排为启发式 + {MAIN_RULER} 主尺 IC 校准,随 regime 漂移;"
            f"L3/L4 为 Claude 推理产出;仅供研究,非投资建议 → "
            f"{appendix_link('完整局限', 'limitations')}"]

    body = "\n".join(out)
    banner = _render_banner(review_result(model, body), aggregate=True)
    return f"{banner}\n{body}" if banner else body


def _same_chain_line(rows: list[dict]) -> str:
    """同链对比:表 → 一行(§5 第 12 行)。同申万一级 ≥2 只 = 1 个 bet 的相关性提示。

    直接从行算,**不解析** `_same_chain_block` 的表格文本 —— 目标价单元格自带嵌套括号
    (`8.44–8.62(对 T+1 收盘 −0.9%~+1.2%)`),正则剥不干净;而且目标价在候选表里已有一份。
    """
    by_sec: dict[str, list[dict]] = {}
    for r in rows:
        sec = str(r.get("sector") or r.get("industry") or "").strip()
        if sec and sec != "nan":
            by_sec.setdefault(sec, []).append(r)
    multi = {s: rs for s, rs in by_sec.items() if len(rs) >= 2}
    if not multi:
        return ""
    parts = []
    for sec in sorted(multi, key=lambda s: -len(multi[s])):
        names = " / ".join(f"{r.get('name', '')} {r.get('rating', '—')}"
                           for r in sorted(multi[sec], key=_sortkey))
        parts.append(f"{sec} ×{len(multi[sec])} = 1 个 bet({names})")
    return "🔗 同链:" + " ｜ ".join(parts)


def _sector_top3_compact(block: str) -> str:
    """行业 top3:表保留,把长方法段换成语义链接(方法全文 → appendix F)。"""
    lines = [ln for ln in block.splitlines() if not ln.startswith("_资格门")]
    head = "## 行业 top3(确定性 healthy 分)"
    body = [ln for ln in lines if not ln.startswith("## ")]
    return "\n".join([head, *body,
                      f"_{method_link('行业资格门', 'sector-top3')};只进 L5,不喂 L3/L4_"]).strip()


def build_summary(scan_dir: Path, analysis_date: str, hhmm: str, folder: str,
                  pinned_path: str | Path | None = None) -> str:
    """兼容薄壳:prepare → render(+ 落 gate_fires)。

    publisher 走的是 `prepare_report_model` / `render_summary` / `render_appendix` 三段式
    (一次 prepare 两处渲染);这个壳留给老调用方与测试,**语义等价但会多跑一次 prepare**。
    """
    model = prepare_report_model(scan_dir, analysis_date, hhmm, folder, pinned_path=pinned_path)
    summary = render_summary(model)
    finalize_review_artifacts(scan_dir, attach_review(model, summary))
    return summary
