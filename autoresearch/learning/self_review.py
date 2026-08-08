#!/usr/bin/env python3
"""发布前机械自检硬门(UZI「self-review gate」本地版)· 纯函数可自测,零 LLM。

把"违背已学经验 / 评级-因子矛盾 / 覆盖不足 / 行业过度集中 / 空泛话术"做成发布前的机械检查:
**有 fail 就不该直接发,先修根因**(assemble 把 fail 顶到报告最前作 banner;skill 据此先改)。
与闭环耦合:经验红线直接来自 factor_lab 的 T+1 IC 校准(winner_rate 满=抛压、过热=回避)+
`feedback_store` 的结构化 guard(lesson 带 {field,op,value} 时自动纳入)。

用法:uv run --no-sync python -m autoresearch.learning.self_review --selftest
"""
from __future__ import annotations

import re
import sys
from collections import Counter

_BUY = ("Overweight", "Buy")
_BANNED = ("基本面良好", "前景广阔", "值得关注", "建议关注")
_TIER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
_RANK = {r: i for i, r in enumerate(_TIER)}  # 越小越多头

# Wave9 B-3:研报体段名/缺档声明锚 —— product_shape_lint 探针 10 与 l4-card.md 模板措辞
# 的单一事实源(tests/test_agent_defs.py::test_l4_card_research_body_anchors_synced 据此
# 钉死两边不脱钩:模板改名而这里不同步改 = agent 照旧写新名字的段,lint 永远读不到)。
_RESEARCH_BODY_HDR = "研报体(档案δ)"
_MICRO_REPORT_HDR = "微研报"
_NO_DOSSIER_DECL = "档案未建"

# T17(design: 2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md §A5):卡契约 v4
# 机器契约标记行 —— 标记行本体进模板是 T24 的事,这里只钉住检查读的字符串(单一事实源,
# 防两处各写一遍走漂)。
_CARD_V4_MARKER = "〔卡契约 v4·隔夜 c1→o2〕"


def _num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _guard_hit(v: float, gd: dict) -> bool:
    op, thr = gd.get("op"), _num(gd.get("value"))
    if thr is None:
        return False
    return {">": v > thr, ">=": v >= thr, "<": v < thr, "<=": v <= thr,
            "==": v == thr}.get(op, False)


def intel_query_cap_lint(scan_dir, cap: int = 15) -> list[dict]:
    """情报稿自报查询数 vs 配置 cap 对账(product_shape_lint 探针 10 的素材)。

    `l4-intel` 的声明行本来就写「网查 N 条」,但全仓此前**没有任何消费者**读它 ——
    2026-07-24 实测 11 稿自报 18/18/17/15/20/26/23/16/17/21/25(cap=15)→ **10 只超限**,
    最高 26 条 = cap 的 173%,而限频「形同虚设」这件事只在 pr_20260714_007 里挂着没人验。

    返回逐码 `{"code", "claimed", "cap"}`;`claimed=None` = 稿里根本没自报,**同样上报**
    ——缺字段是弱证据,不得以缺推断合规。无 intel 稿 → `[]`(presence-gated)。
    """
    import re
    from pathlib import Path

    d = Path(scan_dir)
    out: list[dict] = []
    for p in sorted(d.glob("_l4_intel_*.md")):
        code = p.stem.replace("_l4_intel_", "")
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 读不了按未自报处理,不静默跳过
            out.append({"code": code, "claimed": None, "cap": cap})
            continue
        m = re.search(r"网查\s*(\d+)\s*条", text)
        if m is None:
            out.append({"code": code, "claimed": None, "cap": cap})
        elif int(m.group(1)) > cap:
            out.append({"code": code, "claimed": int(m.group(1)), "cap": cap})
    return out


def review(ctx: dict) -> dict:
    """机械自检。ctx: {finalists:[{code,rating,composite,winner_rate,pct_60d,rsi6,sector,override?}],
    n_cards_expected, n_cards_present, summary_text, lessons:[{id,guard?}], 阈值可选}。

    返回 {ok, n_fail, n_warn, failures:[{check,severity,detail}]}。severity ∈ {fail,warn}。
    """
    failures: list[dict] = []

    def add(check, sev, detail, code=None):
        failures.append({"check": check, "severity": sev, "detail": detail, "code": code})

    finals = ctx.get("finalists", [])
    buys = [f for f in finals if f.get("rating") in _BUY]
    cov_min = ctx.get("coverage_min", 0.8)
    comp_floor = ctx.get("composite_floor", 30.0)
    sec_max = ctx.get("sector_max", 0.6)

    # 1) 覆盖率不足(缺卡太多)
    exp, pres = ctx.get("n_cards_expected", 0), ctx.get("n_cards_present", 0)
    if exp and pres / exp < cov_min:
        add("覆盖率不足", "fail", f"决策卡 {pres}/{exp} < {cov_min:.0%}")

    # 2) 经验红线 + 评级-因子矛盾(只查买单)
    for f in buys:
        code = f.get("code", "?")
        if f.get("override"):
            continue
        wr, comp = _num(f.get("winner_rate")), _num(f.get("composite"))
        p60, rsi = _num(f.get("pct_60d")), _num(f.get("rsi6"))
        if wr is not None and wr > 88:
            add("经验红线·获利盘满", "fail",
                f"{code} 买入但 winner_rate {wr:.0f}>88(IC:抛压/见顶),需特批 override", code=code)
        if p60 is not None and rsi is not None and p60 > 50 and rsi > 80:
            add("经验红线·过热", "warn", f"{code} 买入但过热(60日 {p60:.0f}% + RSI6 {rsi:.0f}>80)",
                code=code)
        if comp is not None and comp < comp_floor:
            add("评级-因子矛盾", "warn", f"{code} 买入但 composite {comp:.0f} < {comp_floor:.0f}",
                code=code)

    # 3) 行业过度集中(≥2 只买单才有意义)
    if len(buys) >= 2:
        secs = Counter((f.get("sector") or f.get("industry") or "?") for f in buys)
        top_share = secs.most_common(1)[0][1] / len(buys)
        if top_share > sec_max:
            add("行业过度集中", "warn",
                f"买单单板块 {secs.most_common(1)[0][0]} 占 {top_share:.0%} > {sec_max:.0%}")

    # 4) 空泛话术(UZI 招牌:禁止和稀泥)
    hit = [b for b in _BANNED if b in (ctx.get("summary_text") or "")]
    if hit:
        add("空泛话术", "warn", f"summary 含禁用词 {hit} → 改成有冲突感的定量金句")

    # 5) lessons 结构化 guard(feedback_store 的经验带 {field,op,value} 时自动纳入硬门)
    for lsn in ctx.get("lessons", []):
        gd = lsn.get("guard")
        if not isinstance(gd, dict):
            continue
        for f in buys:
            if f.get("override"):
                continue
            v = _num(f.get(gd.get("field")))
            if v is not None and _guard_hit(v, gd):
                add(f"违背经验·{lsn.get('id', '?')}", "fail",
                    f"{f.get('code', '?')} 触发经验红线 {gd.get('field')}{gd.get('op')}{gd.get('value')}",
                    code=f.get("code", "?"))

    # 6) 评级超 rubric 评分卡建议(C·LLM-as-judge:防 gestalt 过度多报;有 偏离/override 说明则豁免)
    for f in buys:
        if f.get("override") or f.get("rubric_dev"):
            continue
        rs, rt = f.get("rubric_suggest"), f.get("rating")
        if rs in _RANK and rt in _RANK and _RANK[rt] < _RANK[rs]:
            add("评级超rubric", "warn",
                f"{f.get('code', '?')} 评级 {rt} 激进于评分卡建议 {rs}(需 **偏离** 说明或下修)",
                code=f.get("code", "?"))

    # 7) regime 漂移(assemble 传 detect_drift 的 reason;仅 drift 时传)→ warn,提示重校准
    rd = ctx.get("regime_drift")
    if rd:
        add("regime 漂移", "warn", str(rd))

    # 8) 流程完备性(编排 lint:LLM 段可能被静默跳过;阈值防合成小 fixture 误报)
    fl = ctx.get("flow") or {}
    if fl:  # noqa: SIM102 — 外层 guard 留作恢复买单 skeptic lint(届时块内会有多个 if)
        # 买单 skeptic 已按用户决定移除(2026-07-06)——原 fail lint「买单>0 而 verify.csv 空」停用。
        # 恢复:取消下方三行注释,即恢复"每只 ≥OW 买单发布前必须独立 skeptic 证伪"硬门。
        # if fl.get("buys_n") and not fl.get("verify_n"):
        #     add("流程完备性·买单未过skeptic", "fail",
        #         f"{fl['buys_n']} 只 ≥OW 买单但 verify.csv 空——每只买单发布前必须独立 skeptic 证伪")
        if fl.get("finalists_n", 0) >= 5 and not fl.get("has_market_view"):
            add("流程完备性·策略师未跑", "warn",
                "market_view.md 缺——L3/L4 少了地形块(可选段;真实跑动建议补上)")

    n_fail = sum(1 for x in failures if x["severity"] == "fail")
    return {"ok": n_fail == 0, "n_fail": n_fail, "n_warn": len(failures) - n_fail,
            "failures": failures}


def card_contract_lint(scan_dir) -> list[dict]:
    """卡片契约 lint(编排可靠性;design: run-reliability §1)。全 warn,不阻发布。

    07-02 实证:2 张满卡都没写『进入P4倾向』行(p4_seen=0)——LLM 段契约靠 playbook
    嘱咐会被忘,必须机器抓。复用卡(机器写)跳过;早停卡免 P4 检。
    """
    import re
    from pathlib import Path
    scan_dir = Path(scan_dir)
    base = scan_dir / "details"
    if not base.is_dir():
        return []
    p4_re = re.compile(r"进入P4倾向[:：]")
    out: list[dict] = []
    for p in sorted(base.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        code = p.stem
        if "♻️" in text and "复用" in text:
            continue
        # 早停豁免只认文本首行标题〔早停·表面 DD〕——正文杂散「早停」小标题不豁免(防吞满卡 warn)
        first_line = text.split("\n", 1)[0]
        if ("早停因" not in text and "早停" not in first_line
                and not p4_re.search(text)):
            out.append({"check": "卡片契约·P4倾向缺失", "severity": "warn", "code": code,
                        "detail": f"{code} 满卡未记『进入P4倾向: <Rating>』(阶段效能计量断供)"})
        has_cov = False
        try:  # Wave3 ④→Fix1(review R1 important):与注入器 _dossier_summary_mark 同门
            from autoresearch.dossier import schema as _dsch
            has_cov = bool(_dsch.injectable_summary(code))
        except Exception:  # noqa: BLE001 — 档案层可选
            has_cov = False
        if has_cov:
            if "档案对账" not in text:
                out.append({"check": "卡片契约·档案对账缺失", "severity": "warn", "code": code,
                            "detail": f"{code} 有覆盖档案但卡片无『档案对账』节"
                                      "(驱动/风险/判例逐条核对,增量研究契约)"})
        elif "变化项" not in text:
            try:
                from autoresearch.scan.dossier import render_dossier
                if render_dossier(code, scan_root=scan_dir.parent, exclude=scan_dir.name):
                    out.append({"check": "卡片契约·变化项缺失", "severity": "warn", "code": code,
                                "detail": f"{code} 有个股档案但卡片无『变化项(vs 档案)』节(增量研究契约)"})
            except Exception:  # noqa: BLE001 — 档案层可选
                pass
    return out


def _intel_audit_text(p) -> str:
    """intel 稿的**审计用**全文 —— 若存在裁剪前留档(`<stem>.pretrim`)优先读它,
    否则读 `p`(canonical,可能已被 TRIMMED 原地覆写砍掉部分事件行)本身。

    W9-B2-fix:`autoresearch.scan.l4.intel_guard.guard_intel` 的 TRIMMED 分支会
    原地覆写、真删掉被砍的事件行(不像旧 REJECTED 靠改名保留整稿全文)——
    `intel_future_dates_lint`/`intel_recency_lint` 若只读 canonical 文件,会对被砍
    行永久失明,而被砍的(背景/>1周)恰是这两条 lint 最想抓的对象。真发生裁剪时
    (`dropped_rows>0`)`guard_intel` 会把裁前全文单独留档到 `<code>.pretrim`
    (故意不带 `.md`,对本文件及全仓其他 `_l4_intel_*.md`/`*.md` 裸 glob 都不可见,
    不会被当成第二份独立情报稿重复计数/审计——见
    tests/learning/test_product_shape_lint.py::test_intel_pretrim_archive_not_counted)。
    """
    archive = p.with_name(f"{p.stem}.pretrim")
    return archive.read_text(encoding="utf-8") if archive.exists() else p.read_text(encoding="utf-8")


def intel_future_dates_lint(scan_dir, date_str: str) -> list[dict]:
    """intel as-of 前视机检(advisory;design: 2026-07-12-l4-intel-station-plan.md Task 6)。

    逐 `_l4_intel_*.md`(l4-intel 盲搜落稿)**只查「## 事件段」表格行首日期列**
    (`| YYYY-MM-DD | ... |`)——事件**正文**里提到的未来催化时点(如「8-15 披露中报」)合法,
    不算前视穿越,不查。命中晚于扫描日的表格行日期 → `severity="warn"`(advisory,不挡发布)。

    scan_dir:通常 = `context/scan/<date>`;date_str 按 `dump_gate_fires` 同款惯例由调用方传
    `scan_dir.name`(数据日,`YYYY-MM-DD`)。缺 `_l4_intel_*.md`(未启用/未派发)→ 空列表。
    审计文本经 `_intel_audit_text` 取(W9-B2-fix:TRIMMED 稿存在裁前留档时读留档)。
    """
    import re
    from pathlib import Path
    scan_dir = Path(scan_dir)
    out: list[dict] = []

    def add(check, sev, detail, code=None):
        out.append({"check": check, "severity": sev, "detail": detail, "code": code})

    for p in sorted(scan_dir.glob("_l4_intel_*.md")):
        in_events, future = False, []
        for line in _intel_audit_text(p).splitlines():
            if line.startswith("## 事件段"):
                in_events = True
                continue
            if in_events and line.startswith("## "):
                break
            m = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line) if in_events else None
            if m and m.group(1) > date_str:
                future.append(m.group(1))
        if future:
            add("intel_future_dates", "warn",
                f"{p.name} 事件段含晚于扫描日的日期:{','.join(future[:3])}",
                code=p.stem.replace("_l4_intel_", ""))
    return out


_INTEL_WINDOWS = ("T0", "24h", "背景", "催化挂")
# 日历天口径(不是交易日):">1 周" 取 7 天,与契约里给 agent 的说法逐字一致。
_INTEL_STALE_DAYS = 7
# 各时效窗允许的日期跨度(天):宽松取并集 —— T0 与 24h 无法从**日期**区分(同一天盘后
# 与当天白天都是 gap 0),探针不该假装分得清;分不清的地方就不报。
_INTEL_WINDOW_SPAN = {"T0": (0, 0), "24h": (0, 1), "背景": (1, _INTEL_STALE_DAYS)}


def intel_recency_lint(scan_dir, date_str: str) -> list[dict]:
    """intel 时效三窗机检(advisory;Wave7 批 N §4.3)。三条:

    1. **时效窗与日期对账**:`T0/24h/背景` 三档各有允许的日期跨度,标错 → warn
       (`催化挂` 指向将来时点,无法由过去日期证伪,一律放行)。
    2. **T0 面缺失**:声明行没有 `T0面=` 字段 → warn。写「盘后无增量」是合法结论,
       **留空才是违规** —— 缺字段分不清「查了没料」与「根本没查」,而 T0(收盘→跑报)
       是超短 T+2 主尺下唯一能改变明天开盘定价的窗口。
    3. **净分未按时效衰减**:>1 周(日历 7 天)的事件净分非 0 且时效窗不是 `催化挂` → warn。
       一周前的旧闻在 D+1 不产生增量买盘(07-27 实测有稿把「已消化超 1 周」的预告仍打 +1)。

    **旧契约稿 presence-gated 跳过**:事件段没有任何一行的第 2 列命中三窗词 → 判为
    Wave7 前的旧格式(表头是「2日内可发酵?」),整份跳过不报 —— 新探针不该对着历史存量稿
    刷屏(那是 07-27 十五连报的同一种病)。一切异常路径返回已积累结果,绝不抛。

    审计文本经 `_intel_audit_text` 取(W9-B2-fix:TRIMMED 稿存在裁前留档时读留档,
    否则本条 3 的净分未衰减检查恰好会对被优先砍掉的背景/>1周行永久失明)。
    """
    import re
    from datetime import datetime
    from pathlib import Path

    scan_dir = Path(scan_dir)
    out: list[dict] = []

    def add(check, sev, detail, code=None):
        out.append({"check": check, "severity": sev, "detail": detail, "code": code})

    try:
        as_of = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return out

    row_re = re.compile(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]*?)\s*\|(.*)\|\s*([+-]?\d+(?:\.\d+)?)\s*\|\s*$")
    for p in sorted(scan_dir.glob("_l4_intel_*.md")):
        code = p.stem.replace("_l4_intel_", "")
        try:
            text = _intel_audit_text(p)  # W9-B2-fix:TRIMMED 稿存在裁前留档时读留档
        except Exception:  # noqa: BLE001
            continue
        rows, in_events = [], False
        for line in text.splitlines():
            if line.startswith("## 事件段"):
                in_events = True
                continue
            if in_events and line.startswith("## "):
                in_events = False
            if not in_events:
                continue
            m = row_re.match(line.strip())
            if m:
                rows.append((m.group(1), m.group(2).strip(), float(m.group(4))))
        if not any(w in _INTEL_WINDOWS for _, w, _ in rows):
            continue                      # 旧契约稿(Wave7 前)→ 整份跳过,不报
        bad_win, stale = [], []
        for d, win, score in rows:
            try:
                gap = (as_of - datetime.strptime(d, "%Y-%m-%d")).days
            except ValueError:
                continue
            if gap < 0:
                continue                  # 前视由 intel_future_dates_lint 管,不重复报
            span = _INTEL_WINDOW_SPAN.get(win)
            if span and not (span[0] <= gap <= span[1]):
                bad_win.append(f"{d}({win},实距 {gap}d)")
            if gap > _INTEL_STALE_DAYS and win != "催化挂" and score != 0:
                stale.append(f"{d}({score:+g})")
        if bad_win:
            add("intel_window_mismatch", "warn",
                f"{p.name} 时效窗与日期不符:{'、'.join(bad_win[:3])}", code=code)
        if stale:
            add("intel_stale_score", "warn",
                f"{p.name} >1周事件净分未衰减到 0(且未标 催化挂):{'、'.join(stale[:3])}", code=code)
        if "T0面=" not in text:
            add("intel_t0_missing", "warn",
                f"{p.name} 声明行缺 `T0面=` —— 分不清「盘后无增量」与「没查」", code=code)
    return out


# Wave11 D4:给 T21 台账(docs/research/2026-08-07-skill-tombstone-ledger.md)装自动守卫——
# 防止「文档还在教人跑一个已经不存在的命令/开关」这类回归。RETIRED_SYMBOLS 与墓碑标记词表
# 是本波跨任务已核实的契约,这里不自行增删。
RETIRED_SYMBOLS = ["observe_watchlist", "scan.progress", "l4_reuse",
                   "stable_context_blocks", "sector_brief_mode", "redteam_prob",
                   "analyze-ticker", "watchlist_trigger"]
_DOC_LINT_SUBDIRS = (("skills", ".md"), ("agents", ".md"), ("workflows", ".js"))


def retired_symbol_lint(root=".claude") -> list[dict]:
    """退役符号「指令性引用」lint(Wave11 D4)。

    `RETIRED_SYMBOLS` 逐个在 `.claude/{skills,agents,workflows}` 的 `.md`/`.js` 文件里找——
    某行**含**退役符号 **且该行本身不含**退役标记(`已退役|已移除|勿再|已废弃|退役`)→ fail。

    **墓碑白名单按行、不按文件**:同一份文档里可以既有「observe_watchlist 已退役,勿再跑」
    (墓碑,放行)又有「跑 observe_watchlist」(活指令,拦下)——一处墓碑不得为全文件背书,
    否则退役符号只要在文件任意角落提过一次「已退役」,后面几百行怎么教人用它都会被放行。

    扫描域按扩展名限定为 `.md`(skills/agents)与 `.js`(workflows)——那两类才是「文档」,
    `.jsonc` 配置注释、`.omc` 之类工具态文件不算(会把无关噪声当成文档 bug 报出来)。

    presence-gated:root/子目录不存在、坏文件 → 静默跳过,绝不抛异常。
    """
    import re
    from pathlib import Path

    root = Path(root)
    mark_re = re.compile(r"已退役|已移除|勿再|已废弃|退役")
    out: list[dict] = []
    for sub, ext in _DOC_LINT_SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob(f"*{ext}")):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001 — 读不了按无内容处理,不炸
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                hit = [s for s in RETIRED_SYMBOLS if s in line]
                if hit and not mark_re.search(line):
                    out.append({
                        "check": "产物形状·退役符号指令性引用",
                        "severity": "fail",
                        "detail": f"{p.name}:{i} 含退役符号 {'/'.join(hit)} 但本行无退役标记"
                                  "——疑似仍在教人跑/配置已退役对象(墓碑标记须同行)",
                        "code": None,
                    })
    return out


_AGENT_DEFAULTS_MARK = "const AGENT_DEFAULTS = {"


def workflow_literal_lint(root=".claude") -> list[dict]:
    """workflow `agent()` 调用点内联 model/effort 字面量 lint(Wave11 D4,承 Wave11-B2)。

    Wave11-B2 把 scan-market.js/l4-stock.js/dossier-init.js 三个 workflow 的 model/effort
    单一事实源收进各自顶部的 `const AGENT_DEFAULTS = {...}` 表 + `AG(role)` 解析器
    (`tests/test_agent_defs.py::test_workflow_shell_wrappers_use_agent_defaults` 已用单测锁住
    model 字面量不得溢出该表)。本探针把同一判据接进 product_shape_lint 做生产自动守卫
    (不再只靠人主动跑单测),并补上 `effort:` 字面量。规则:`model:`/`effort:` 字面量出现在
    `AGENT_DEFAULTS` 块**之外** → fail。

    ⚠️ 判块不能靠 `grep -v AGENT_DEFAULTS` 之类的字符串排除法——表内每一行(如
    `gp_shell: { model: 'sonnet', effort: 'low' },`)本身并不含 "AGENT_DEFAULTS" 这个词,
    必须真正算出块的起止行号区间,再判目标行是否落在区间内。

    没有 `const AGENT_DEFAULTS = {` 表的 workflow(如 t1-review.js 走独立的
    `cfg.agents.t1_diag/t1_synth` 通道,见该文件顶部注)天然不受本规则约束——presence-gated
    跳过,不是本规则的检查对象,防止跟另一套架构打架。

    presence-gated:root/workflows 不存在、坏文件 → 静默跳过,绝不抛异常。
    """
    import re
    from pathlib import Path

    root = Path(root) / "workflows"
    out: list[dict] = []
    if not root.is_dir():
        return out
    lit_re = re.compile(r"\b(?:model|effort)\s*:\s*['\"]")
    for p in sorted(root.glob("*.js")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        start = text.find(_AGENT_DEFAULTS_MARK)
        if start == -1:
            continue                                  # 无该表 → 本规则不适用(如 t1-review.js)
        close = text.find("\n}\n", start)
        if close == -1:
            continue                                  # 表未闭合,交给 test_agent_defs.py 的结构性测试抓
        block_start_line = text.count("\n", 0, start) + 1
        block_end_line = text.count("\n", 0, close + 1) + 1
        for i, line in enumerate(text.splitlines(), start=1):
            if block_start_line <= i <= block_end_line:
                continue                              # 块内合法字面量
            if lit_re.search(line):
                out.append({
                    "check": "产物形状·workflow内联字面量",
                    "severity": "fail",
                    "detail": f"{p.name}:{i} 在 AGENT_DEFAULTS 表外出现内联 model/effort 字面量"
                              "——单一事实源被绕过,调用点应改用 `...AG(role)`",
                    "code": None,
                })
    return out


#: 旧主尺字面量。2026-08-05 用户裁定换 `gap_c1_o2` 后它降为**参考尺**,不删——所以判据不是
#: "出现即违规",而是"出现却没说清自己是参考尺/沿革"。
_STALE_RULER_TOKEN = "fwd_2_oc"
#: 合法写法的标记词(任一在场即放行)。**先给合法情形一个标记,再谈加严检查**——否则
#: T13 刚写好的 16 处沿革/参考尺注记会被自己的 lint 天天判违规(2026-07-27 家训:修法
#: 排序 = 补指令 > 给合法情形一个标记 > 才是加严检查)。
_STALE_RULER_OK_MARKS = ("参考尺", "沿革", "旧尺", "旧主尺", "历史读数", "命名沿自",
                         "降参考", "不改写", "未复测", "回滚杆", "勿随主尺漂移",
                         "不随主尺漂移", "固定列名", "当时是", "当时的主尺", "取代")
#: 「活指令」判据:同一行**既提旧尺又提「主尺」**,却不带任何合法标记 → 把旧尺当现行主尺讲。
#:
#: ⚠️ 判据故意是"共现",不是精巧句式匹配。第一版写成 `主尺仍 fwd_2_oc` 一类的正则,拿 T13
#: **之前**的真实违规行回测,8 条只逮到 3 条(漏掉「fwd_2_oc 超短主尺 IC 校准」「裁定
#: fwd_2_oc 主尺」「已实现 fwd_2_oc(事后,超短主尺」这类语序)——一个逮不住自己那条病的
#: 探针就是假绿灯。`test_stale_ruler_recall_on_real_pre_t13_offenders` 把 8/8 召回钉死。
_STALE_RULER_LIVE_MARK = "主尺"
#: 扫描域:文档 + 代码。二进制/产物目录不在内(lint 只管人写的东西)。
_STALE_RULER_EXTS = (".py", ".md", ".js", ".json", ".jsonc", ".yaml", ".yml", ".toml")
#: 规则②的活文档域:`.claude/{skills,agents,workflows}` + `docs/PANORAMA.md`。PANORAMA 必须在
#: 内 —— 本病最刺眼的那处(:704「权重校准主尺仍 fwd_2_oc」)就长在它身上,只守 `.claude/`
#: 等于守错门。历史 specs/plans/research 报告**不在域内**(它们是审计记录,按 T13 裁定不改写)。
_STALE_RULER_LIVE_DOCS = ("docs/PANORAMA.md",)


def _git_new_files(root) -> list[str] | None:
    """工作树里的**新增**文件(相对 root 的 posix 路径);非 git / git 不可用 → `None`。

    `git status --porcelain` 的 `A`(已 add 未 commit)与 `??`(未跟踪)两档才算新增;
    `M`/`R` 等**改动**不算——本 lint 的粒度是"新写的文件要按新尺写",不是"碰过的文件
    都要回头改",否则 286 处历史命中会天天报警(存量不追溯是硬要求)。
    """
    import subprocess
    from pathlib import Path

    root = Path(root)
    if not root.is_dir():
        return None
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                           cwd=root, capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 — 无 git / 超时 → 按"判不了"处理,不炸
        return None
    if r.returncode != 0:
        return None                      # 不是 git 仓库
    out: list[str] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if code.strip() in ("A", "AM", "??"):
            if " -> " in path:           # rename 形式,取目标
                path = path.split(" -> ", 1)[1]
            out.append(path.strip('"'))
    return out


def stale_ruler_lint(root=".") -> list[dict]:
    """旧尺(`fwd_2_oc`)裸写防复发 lint(Wave12 T14,承 T13 大扫)。

    T13 把文档层 286 处旧尺命中逐条归类完(历史审计记录 / 参考尺注记 / 沿革注记),但
    **没有任何东西阻止下一个人重新写一处**——最刺眼的 PANORAMA:704「权重校准主尺仍
    fwd_2_oc」正是这么长出来的。本探针补上那道门,两条判据:

    **① 新增文件裸写**(`git status` 的 `A`/`??` 粒度):新写的文件里出现 `fwd_2_oc`
    却没有任何 `_STALE_RULER_OK_MARKS` 标记 → fail。**存量文件一律不追溯**(已 commit
    的、乃至被改动过的都不算)——否则 286 处历史命中会把这条 lint 变成天天响的噪声,
    而"天天响的警报"等于没有警报。

    **② `.claude/` 活指令句式**(不论文件新旧):把旧尺当**现行主尺**讲的句子
    (`主尺仍 fwd_2_oc` / `fwd_2_oc 为主尺`)→ fail。这一条与"新增"无关:存量 skill 文档
    里写出来同样是在指挥 agent 用错尺,而 skill 文档正是 agent 当契约读的东西。
    沿革写法(「当时是 `fwd_2_oc`,现 `gap_c1_o2`」)带标记词,天然放行。

    presence-gated:非 git 目录 / 无 git 可执行 / 坏文件 → 静默跳过,绝不抛异常
    (与 `retired_symbol_lint` 同姿势)。
    """
    from pathlib import Path

    root = Path(root)
    out: list[dict] = []
    if not root.is_dir():
        return out

    def _ok(text: str) -> bool:
        return any(m in text for m in _STALE_RULER_OK_MARKS)

    # ① 新增文件裸写
    for rel in _git_new_files(root) or []:
        if not rel.endswith(_STALE_RULER_EXTS):
            continue
        p = root / rel
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 读不了按无内容处理,不炸
            continue
        if _STALE_RULER_TOKEN not in text or _ok(text):
            continue
        line_no = next((i for i, ln in enumerate(text.splitlines(), start=1)
                        if _STALE_RULER_TOKEN in ln), 1)
        out.append({
            "check": "产物形状·旧尺裸写",
            "severity": "fail",
            "detail": f"{Path(rel).name}:{line_no} 新增文件裸写 `{_STALE_RULER_TOKEN}` 却无"
                      "「参考尺/沿革」注记——主尺自 2026-08-05 起是 `gap_c1_o2`"
                      "(common.ruler.MAIN_RULER 单点);要么改用主尺,要么写明这是参考尺",
            "code": None,
        })

    # ② 活文档里的「旧尺当主尺讲」(不论文件新旧)
    targets: list = []
    claude = root / ".claude"
    if claude.is_dir():
        for sub, ext in _DOC_LINT_SUBDIRS:
            d = claude / sub
            if d.is_dir():
                targets.extend(p for p in sorted(d.rglob(f"*{ext}")) if p.is_file())
    targets.extend(p for p in (root / rel for rel in _STALE_RULER_LIVE_DOCS) if p.is_file())
    for p in targets:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if _STALE_RULER_TOKEN in line and _STALE_RULER_LIVE_MARK in line and not _ok(line):
                out.append({
                    "check": "产物形状·旧尺裸写",
                    "severity": "fail",
                    "detail": f"{p.name}:{i} 同行既写 `{_STALE_RULER_TOKEN}` 又写「主尺」却无"
                              "「参考尺/沿革」标记——疑似把旧尺当**现行主尺**讲(活指令);"
                              "现主尺 `gap_c1_o2`(2026-08-05 用户裁定)。若在记沿革,同行标注即可",
                    "code": None,
                })
    return out


def card_v4_marker_lint(scan_dir, date_str: str) -> list[dict]:
    """v4 卡契约口径声明缺失 lint(T17;design A5)。

    **直面语义坍缩**:隔夜窗内唯一实现价=T+2 开盘,盘中目标价/止损在这把尺下不会被系统
    执行,从「预测窗内触达」降格为「入场论据」——这不是文字调整。分界日
    (`ruler.SCHEMA_SWITCH_V4`)**起**产出的卡理应用机器契约标记行
    `〔卡契约 v4·隔夜 c1→o2〕` 声明自己活在哪套口径下,逐 `details/*.md` 查缺失 → warn。

    标记行本体进模板是下一个 task(T24)的事,本函数只加检查、不写模板。`date_str` 取
    scan 目录日期(同 `dump_gate_fires`/`intel_future_dates_lint` 惯例,一次 scan 跑同一天,
    不逐卡各自判断)——**分界日前的卡不触发**(旧卡没有这行,不是它的契约,不该被冤枉)。

    presence-gated:`date_str` 早于分界日、无 `details/`、坏文件 → 静默跳过,绝不抛异常。
    """
    from pathlib import Path

    from autoresearch.common.ruler import SCHEMA_SWITCH_V4

    out: list[dict] = []
    if str(date_str) < SCHEMA_SWITCH_V4:
        return out
    scan_dir = Path(scan_dir)
    base = scan_dir / "details"
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 读不了按缺声明处理,不炸
            text = ""
        if _CARD_V4_MARKER not in text:
            code = p.stem.split(".")[0]
            out.append({
                "check": "卡契约v4·口径声明缺失", "severity": "warn", "code": code,
                "detail": f"{code} 分界日({SCHEMA_SWITCH_V4})起的卡缺 `{_CARD_V4_MARKER}` "
                          "标记行——隔夜口径未声明,读者分不清目标价是执行价还是入场论据",
            })
    return out


def product_shape_lint(scan_dir, date_str: str) -> list[dict]:
    """产物形状 lint(十三探针,零 LLM;design: 2026-07-13-next-optimization-survey.md 线 C
    + 2026-07-22 dossier design Wave1 ⑤ + 2026-07-23 终审 I-2 + Wave9 B-3 + Wave11 D4 + T17)。

    把停车场里"已知的产物形状病"装成每跑可见的机械断言(advisory 起步,攒够跑数再升):

    1. **保送§2空**(warn):finalists lane∈{pinned,watchlist_trigger} 的票,其 judged 条目
       thesis(及 risk/catalyst 键若存在)为空/缺 → §2 保送表无料可填(07-14 事故:pinned
       的 L3 判断在 merge 处被整段丢弃)。注意 pinned 身份只认 **finalists.csv 的 lane**
       (judged 里存的是原召回 lane,如 trend/growth)。
    2. **force_full 探针**(warn/info):按生产同款 priors(finalists 行 conviction/lane +
       L2 表 n_channels/l2_lane_reserved,调 `l4_card.force_full_card` 真身)算命中集
       (♻️ 复用卡派发时即跳过,不计);命中>0 但 `run_health.l4_phases.n_full`==0 →
       warn「静默未生效」(FN-1 探针:死了也像活着);命中==0 → **info** 显式记账,非静默。
    3. **intel 稿数**(warn):`_l4_intel_*.md` >0 份(=intel 启用)时,稿数(只认
       `_l4_intel_<6位码>.md`,变体如 `*_probe` 不计)≠ 期望 = **全 finalist 行**(含保送——
       保送票同走 l4-stock 链、同派 intel;Wave9 R5 退役 TTL 复用后不再有复用扣减)。
       0 份 intel = 未启用,本条不出。
    4. **anns 双源**(warn/info,Wave9 A-1 + 复核轮1):`anns_source_status.status`——
       `blind`(有稿但双源皆空)= **warn**,公告面只剩 intel 单腿;`fallback` = info(兜底
       承载,显式记账);`ok`/`pending`(L3 阶段还没跑到,不是"双源皆空"的证据)不出条。
       旧 run 无该键 → 回落 `anns_empty_rate` 旧 expected 口径,不追溯误报。
    5. **market_view 防锚定**(warn):market_pack.json 的 sector_healthy_top3 行业名出现在
       market_view.md 文本 → L5 专属看多读数泄漏进策略师稿(闭合 final-review I-1)。
    6. **intel 零URL**(warn):单份 intel 稿 `http(s)://` 计数==0 → 情报不可审计
       (07-14 事故 pr_007:零URL 逐稿逮)。
    7. **引用密度 citation_density**(warn):`details/<code>.md` 满卡带日期引用行数<6 →
       研究底料偏薄(07-21 银河微电实例仅 4 行);早停卡(标题含〔早停)与 ♻️ 复用卡豁免。
    8. **价格断言对账 price_claim_mismatch**(warn,逐码):卡文里对本票的涨跌%/涨停断言
       经 `price_claims.audit_card_text` 与 lake OHLCV 对账,任一条不符 → warn,detail 带
       首条不符断言(pr_20260714_006 同族(本探针读 staging 卡,不含 intel 附录;intel 侧
       断言由 assemble 发布层对账兜底))。
    9. **pinned SELL 双复核 sell_review_missing**(warn,逐码):保送(lane=pinned)持仓卡
       评级 Sell/Underweight 但缺 `_ensemble_<code>.json`(或 trigger≠sell_review)→ 持仓卖出
       双复核静默漏跑(final-review I-2;镜像 probe 3 intel 稿数兜底,防 args.pinned 漏传)。
    10. **研报体缺失**(warn,逐码;Wave9 B-3):`_dossier_present.json`(dispatch 侧按
        `dossier_path` 文件存在写的"本次哪些票有档案可注入"名单)记有档案的票,卡文缺
        「研报体(档案δ)」(满卡)/「微研报」(早停卡)段;或无档案的票缺「档案未建」
        缺档声明行——两侧口径与 `.claude/agents/l4-card.md` 模板措辞同批对齐(先补指令
        后加检查:agent def 已写要求,这里才加检查)。
    11. **退役符号指令性引用**(fail,Wave11 D4):`.claude/{skills,agents,workflows}` 里某行
        含 `RETIRED_SYMBOLS` 但本行无退役标记(墓碑白名单按行、不按文件)——文档还在教人跑
        一个已经不存在的命令/开关,见 `retired_symbol_lint`。
    12. **workflow 内联字面量**(fail,Wave11 D4):`.claude/workflows/*.js` 的 `model:`/
        `effort:` 字面量出现在 `AGENT_DEFAULTS` 表之外——单一事实源被绕过,见
        `workflow_literal_lint`。
    13. **v4 卡契约口径声明缺失**(warn,逐码;T17):分界日(`ruler.SCHEMA_SWITCH_V4`)起的
        卡缺机器契约标记行 `〔卡契约 v4·隔夜 c1→o2〕`——隔夜口径(唯一实现价=T+2 开盘)
        未声明,读者分不清目标价是执行价还是入场论据,见 `card_v4_marker_lint`。

    全部 presence-gated:缺文件/缺键/坏文件 → 该条静默跳过,**绝不抛异常**。
    返回 [{check,severity,detail,code}](severity ∈ {fail,warn,info});接线在 assemble
    (与 card_contract_lint 同点),本函数纯读不写。
    """
    import contextlib
    import json
    import re
    from pathlib import Path
    scan_dir = Path(scan_dir)
    exempt = {"pinned", "watchlist_trigger"}
    out: list[dict] = []

    def add(check, sev, detail, code=None):
        out.append({"check": check, "severity": sev, "detail": detail, "code": code})

    # ── 共享读数(各自 presence-gated;坏文件按缺处理) ──
    fin_rows: list[dict] = []
    fin_loaded = False
    with contextlib.suppress(Exception):
        fp = scan_dir / "finalists.csv"
        if fp.exists():
            import pandas as pd
            fin = pd.read_csv(fp, dtype={"code": str})
            if "code" in fin.columns:
                for _, r in fin.iterrows():
                    raw = str(r.get("code", "") or "").strip()
                    if not raw or raw == "nan":
                        continue
                    fin_rows.append({"code": raw.split(".")[0].zfill(6),
                                     "lane": str(r.get("lane", "") or "").strip(),
                                     "conviction": r.get("conviction")})
                fin_loaded = True
    pinned_codes = [r["code"] for r in fin_rows if r["lane"] in exempt]

    judged: dict[str, dict] = {}
    judged_loaded = False
    with contextlib.suppress(Exception):
        jp = scan_dir / "_l3_judged.json"
        if jp.exists():
            for e in json.loads(jp.read_text(encoding="utf-8")) or []:
                if isinstance(e, dict) and str(e.get("code", "") or "").strip():
                    judged[str(e["code"]).split(".")[0].zfill(6)] = e
            judged_loaded = True

    health: dict = {}
    with contextlib.suppress(Exception):
        hp = scan_dir / "run_health.json"
        if hp.exists():
            h = json.loads(hp.read_text(encoding="utf-8"))
            health = h if isinstance(h, dict) else {}

    reused: set[str] = set()          # ♻️ 复用卡 banner(L4 卡 TTL 复用已退役,
                                      # 仅历史 staging 卡还带这层壳)
    with contextlib.suppress(Exception):
        for p in (scan_dir / "details").glob("*.md"):
            text = p.read_text(encoding="utf-8")
            if "♻️" in text and "复用" in text:
                reused.add(p.stem.split(".")[0].zfill(6))

    intel_files = sorted(scan_dir.glob("_l4_intel_*.md"))
    intel_codes = {m.group(1) for p in intel_files
                   if (m := re.fullmatch(r"_l4_intel_(\d{6})", p.stem))}

    # 1) 保送§2非空(finalists 是 pinned 身份唯一事实源;judged 文件缺 → 整条跳过)
    if judged_loaded:
        for code in pinned_codes:
            e = judged.get(code)
            if e is None:
                add("产物形状·保送§2空", "warn",
                    f"{code} 保送票在 _l3_judged.json 无条目 —— §2 风险/催化无料可填", code=code)
                continue
            empty = [k for k in ("thesis", "risk", "catalyst")
                     if (k == "thesis" or k in e) and not str(e.get(k, "") or "").strip()]
            if empty:
                add("产物形状·保送§2空", "warn",
                    f"{code} 保送票 judged 条目 {'/'.join(empty)} 为空 —— §2 风险/催化列将开天窗",
                    code=code)

    # 2) force_full 探针(生产同款判据真身;缺 l4_phases/n_full 或 finalists → 跳过)
    l4_phases = health.get("l4_phases")
    if fin_loaded and isinstance(l4_phases, dict) and "n_full" in l4_phases:
        with contextlib.suppress(Exception):
            from autoresearch.scan.l4.rubric import force_full_card
            l2_priors: dict[str, dict] = {}
            with contextlib.suppress(Exception):
                l2p = scan_dir / "L2_gbdt_top200.csv"
                if l2p.exists():
                    import pandas as pd
                    l2 = pd.read_csv(l2p, dtype={"code": str})
                    if "code" in l2.columns:
                        l2["code"] = l2["code"].astype(str).str.zfill(6)
                        l2_priors = l2.set_index("code").to_dict("index")
            hits = [r["code"] for r in fin_rows
                    if r["code"] not in reused          # 复用卡不派发,force_full 未评估
                    and force_full_card({**l2_priors.get(r["code"], {}),
                                         "conviction": r["conviction"], "lane": r["lane"]})]
            n_full = int(l4_phases.get("n_full") or 0)
            if hits and n_full == 0:
                add("产物形状·force_full未生效", "warn",
                    f"force_full 命中 {len(hits)} 只({','.join(hits[:5])})但 l4_phases.n_full=0"
                    " —— 强制满卡静默未生效(FN-1:安全网死了也像活着)")
            elif not hits:
                add("产物形状·force_full零命中", "info",
                    f"{date_str} force_full 0 命中(显式记账,非静默)")

    # 3) intel 稿数 = 全 finalist 行(含保送,皆走 l4-stock 链派 intel;Wave9 R5 退役 TTL 复用后无复用扣减)
    if intel_files and fin_loaded:
        n_rows = len(fin_rows)
        expect, cal = n_rows, f"finalist 行 {n_rows}(含保送;R5 后无复用)"
        if len(intel_codes) != expect:
            add("产物形状·intel稿数不符", "warn",
                f"intel 稿 {len(intel_codes)} 份 ≠ 期望 {expect}({cal})")

    # 4) anns 双源探针(Wave9 A-1:存在性 ≠ 有效性 —— "无权限"曾与"当日故障"同形)
    st = health.get("anns_source_status")
    st = st if isinstance(st, dict) else {}   # 坏值(非 dict)按缺处理,回落旧口径,绝不抛
    status = str(st.get("status", "")) if st else ""
    if status == "pending":
        pass  # 复核轮1:L3 阶段还没跑到——既非已核实健康也非已核实故障,这天这件事还没
              # 发生,不出条(既不是 warn 也不是 info)
    elif status == "blind":
        # Wave9 final-fix C-1:兜底源(anns_fallback.fetch_anns)已接线进 harvest_l3_news——
        # 走到这里的 blind 意味着**兜底也真的被查过**、只是同样没查到料,不是"从未接线、
        # 诊断信息断言了没发生的事"那种旧病(修复前 fallback 全仓零生产调用点,此行文案
        # 曾断言一件不可能发生的事)。
        add("产物形状·anns双源盲", "warn",
            "公告面双源皆空(主源无权限 + 兜底源已查但无料)—— 卡片公告证据仅剩 intel 单腿,"
            "非 expected;查兜底源可达性")
    elif status == "fallback":
        from autoresearch.data.sources.anns_fallback import SOURCE_TAG as _FALLBACK_TAG
        add("产物形状·anns兜底承载", "info",
            f"主源空,兜底源({_FALLBACK_TAG})承载 {st.get('fallback_rows', 0)} 行"
            " —— 公告面在场,已记账")
    elif status == "" and health.get("anns_empty_rate") is not None:
        # 旧 run(无 anns_source_status 键)回落旧口径,不误报。"no-permission" 措辞与旧探针
        # 逐字保留(tests/learning/test_product_shape_lint.py::test_anns_expected_info 锁定这个
        # 子串;新老口径共用同一条消息,历史 run 复盘时文案不会突变)。
        with contextlib.suppress(TypeError, ValueError):
            if float(health["anns_empty_rate"]) == 1.0:
                add("产物形状·anns去伪", "info",
                    "anns_empty_rate=1.0 = expected/no-permission(旧 run 无双源状态键,"
                    "按旧 expected 口径回落)")

    # 5) market_view 防锚定(sector_healthy_top3 是 L5 专属,泄漏进策略师稿即锚定通道)
    with contextlib.suppress(Exception):
        mp, mv = scan_dir / "market_pack.json", scan_dir / "market_view.md"
        if mp.exists() and mv.exists():
            top3 = json.loads(mp.read_text(encoding="utf-8")).get("sector_healthy_top3") or []
            inds = [s for r in top3 if isinstance(r, dict)
                    and (s := str(r.get("industry", "") or "").strip())]
            text = mv.read_text(encoding="utf-8")
            hit = [i for i in inds if i in text]
            if hit:
                add("产物形状·market_view防锚定", "warn",
                    f"market_view.md 出现确定性看多 top3 行业名 {hit}"
                    " —— L5 专属读数泄漏进策略师稿(final-review I-1)")

    # 6) intel 零URL(逐稿;含 `_probe` 等变体稿——是稿就该可审计)
    for p in intel_files:
        with contextlib.suppress(Exception):
            if not re.search(r"https?://", p.read_text(encoding="utf-8")):
                add("产物形状·intel零URL", "warn",
                    f"{p.name} 全文 0 条 http(s) URL —— 情报不可审计(来源应带链接)",
                    code=p.stem.replace("_l4_intel_", ""))

    # 10) intel 限频对账(Wave6 Q1-②):声明行自报「网查 N 条」vs 当日 config cap。
    # 07-24 实测 10/11 超限、最高 26/15 —— 这个数一直在写,只是没有消费者(pr_20260714_007)。
    # cap 取当日 echo(改了 config 就按新 cap 对账),缺 echo 回落 15(agent def 默认)。
    _cap = 15
    with contextlib.suppress(Exception):
        _cap = int((json.loads((scan_dir / "user_config_echo.json").read_text(encoding="utf-8"))
                    .get("l4_intel") or {}).get("max_queries") or 15)
    for h in intel_query_cap_lint(scan_dir, cap=_cap):
        _claimed = "未自报查询数" if h["claimed"] is None else f"自报 {h['claimed']} 条"
        add("产物形状·intel限频", "warn",
            f"{_claimed} > cap {h['cap']} —— 限频是指令级、无强制力(pr_20260714_007);"
            f"未自报 = 无法对账,不等于合规",
            code=h["code"])

    # ── 7. 引用密度(Wave1 ⑤-5):满卡带日期引用 <6 行 → warn;早停/♻️复用卡豁免 ──
    # _DATED 收紧为真日历日(final-review I-1):裸支路 \b\d{1,2}[-/]\d{1,2}\b 把 R:R 1.8/1、
    # PE band 20-30、5-10%、3/2/1 全计成日期 → n_cited 虚增、门槛 6 恒绿打不响。收紧后 M/D 支路
    # 要求 月∈1-12、日∈1-31、数字前不接小数/数字(排除 1.8/1)、后不接 %/./数字/斜杠(排除 5-10%、3/2/1);
    # 完整 ISO(20\d{2}-\d{1,2}-\d{1,2})与紧凑(20\d{6})支路保留。
    _DATED = re.compile(
        r"20\d{2}-\d{1,2}-\d{1,2}|20\d{6}"
        r"|(?<![\d.])(?:1[0-2]|0?[1-9])[-/](?:3[01]|[12]?\d)(?![\d.%/])")
    cards: dict[str, str] = {}
    with contextlib.suppress(Exception):
        for p in (scan_dir / "details").glob("*.md"):
            cards[p.stem.split(".")[0].zfill(6)] = p.read_text(encoding="utf-8")
    for code, text in sorted(cards.items()):
        if "〔早停" in text or code in reused:
            continue
        n_cited = sum(1 for ln in text.splitlines() if _DATED.search(ln))
        if n_cited < 6:
            add("citation_density", "warn",
                f"满卡带日期引用仅 {n_cited} 行(<6)——研究底料偏薄(07-21 银河微电 4 行病)",
                code=code)

    # ── 8. 价格断言对账聚合(Wave1 ⑤-2):任一卡有不符断言 → warn(逐码) ──
    name_by_code = {r["code"]: "" for r in fin_rows}
    with contextlib.suppress(Exception):
        import pandas as pd
        fin = pd.read_csv(scan_dir / "finalists.csv", dtype={"code": str})
        for _, r in fin.iterrows():
            c = str(r.get("code", "") or "").split(".")[0].zfill(6)
            name_by_code[c] = "" if pd.isna(r.get("name")) else str(r.get("name"))
    claim_summaries: list[dict] = []
    for code, text in sorted(cards.items()):
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            res = price_claims.audit_card_text(
                text, name=name_by_code.get(code, ""), code6=code, date=date_str,
                bars_fn=price_claims.bars_for)
            if res.get("n_candidate"):
                claim_summaries.append(res)
            if res["mismatches"]:
                b = res["mismatches"][0]
                # 措辞按 dir 分涨/跌停:原先 kind=='limit' 一律播「称 涨停」,于是 600988
                # 那条**跌停**断言在汇总屏上显示成「称 涨停 实 -8.32%」——读者据此推的方向
                # 与卡里写的正好相反(2026-07-27 实锤)。dir 缺失(旧契约手搭 dict)才回退。
                kind_txt = ({1: "涨停", -1: "跌停"}.get(b.get("dir"), "涨停/跌停")
                            if b["kind"] == "limit" else f"{b['claimed']}%")
                add("price_claim_mismatch", "warn",
                    f"{len(res['mismatches'])} 条价格断言与 OHLCV 不符(首条 {b['date']} "
                    f"称 {kind_txt} 实 {b['actual']}%)——pr_20260714_006 型",
                    code=code)

    # A3:当日主语分布落盘(不告警,只计量)——「n 条不符」这个数只有配上分母才有意义,
    # 而漏抽(UNKNOWN_SUBJECT)不落盘就永远是静默的。
    if claim_summaries:
        with contextlib.suppress(Exception):
            import json as _json

            from autoresearch.scan import price_claims
            merged = price_claims.merge_summaries(claim_summaries)
            (scan_dir / "price_claim_subjects.json").write_text(
                _json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")

    # ── 9. pinned SELL 双复核 tripwire(final-review I-2):镜像 intel 稿数兜底(probe 3)──
    # 保送(lane=pinned)持仓卡评级 Sell/Underweight 但缺 _ensemble_<code>.json(或 trigger≠
    # sell_review)→ 持仓卖出双复核静默漏跑(⑤-3 招牌场景:漏传 args.pinned 则单 run Sell 直出)。
    for r in fin_rows:
        if r["lane"] != "pinned":
            continue
        code = r["code"]
        text = cards.get(code)
        if text is None:                       # 卡缺 → presence-gated 跳过
            continue
        rating_line = next((ln for ln in text.splitlines() if "**Rating**" in ln), "")
        if not any(w in rating_line for w in ("Sell", "Underweight")):
            continue
        ens = scan_dir / f"_ensemble_{code}.json"
        trig = None
        with contextlib.suppress(Exception):
            if ens.exists():
                trig = json.loads(ens.read_text(encoding="utf-8")).get("trigger")
        if trig != "sell_review":
            why = "缺失" if not ens.exists() else f"trigger={trig!r}≠sell_review"
            add("sell_review_missing", "warn",
                f"{code} 保送持仓卡评级偏空(Sell/UW)但 _ensemble_{code}.json {why}"
                " —— 持仓 SELL 双复核未跑(⑤-3:漏传 args.pinned?单 run 直出无兜底)", code=code)

    # 10) 研报体在场(Wave9 B-3;先补指令后加检查 —— agent def 已写要求)
    with contextlib.suppress(Exception):
        present = set(json.loads(
            (scan_dir / "_dossier_present.json").read_text(encoding="utf-8")))
        for fr in fin_rows:
            code = str(fr.get("code", "")).zfill(6)
            card = scan_dir / "details" / f"{code}.md"
            if not card.exists():
                continue
            txt = card.read_text(encoding="utf-8")
            if code in present:
                if _RESEARCH_BODY_HDR not in txt and _MICRO_REPORT_HDR not in txt:
                    add("产物形状·研报体缺失", "warn",
                        f"{code} 有档案却无研报体段 —— 素材已注入任务包但卡没写")
            elif _NO_DOSSIER_DECL not in txt:
                add("产物形状·研报体缺失", "warn",
                    f"{code} 无档案且未写缺档声明行")

    # 11)+12) 退役符号指令性引用 + workflow AGENT_DEFAULTS 外字面量(Wave11 D4):与具体
    # scan_dir 无关,查的是仓库 `.claude` 静态树。根按 scan_dir 的祖先目录推导(同
    # usage_reconcile_lint 手法):生产 scan_dir == context/scan/<date> 时上三级 == 仓库根,
    # 与 cwd 相对的 `.claude` 缺省值 byte-identical;测试用的假 scan_dir(tmp_path 下 1-2 级)
    # 上三级自然没有 `.claude`,presence-gated 静默跳过——不靠 monkeypatch.chdir,也不会让
    # 既有 tmp_path 用例被仓库当前 `.claude` 内容的变化摆布。
    claude_root = scan_dir.parent.parent.parent / ".claude"
    with contextlib.suppress(Exception):
        out.extend(retired_symbol_lint(claude_root))
    with contextlib.suppress(Exception):
        out.extend(workflow_literal_lint(claude_root))
    # 14)旧尺裸写防复发(T14):同上按 scan_dir 祖先推**仓库根**(不是 .claude 根——本探针
    # 既查 .claude 文档也查新增代码文件,且要在仓库根上跑 `git status`)。
    with contextlib.suppress(Exception):
        out.extend(stale_ruler_lint(claude_root.parent))

    # 13) v4 卡契约口径声明缺失(T17):标记行本体是 T24 的事,这里只加检查
    with contextlib.suppress(Exception):
        out.extend(card_v4_marker_lint(scan_dir, date_str))
    return out


# 与 usage_reconcile 产物同形的路径常量(避免两处各写一遍字面量走漂):streak 账本默认路径
# 跟 `autoresearch.trace.usage_reconcile.LEDGER_PATH` 保持同一字符串,但故意不 import 该模块
# 顶层(self_review 是纯 lint 层,不想给它添一条对 trace 包的硬依赖——这里只在函数体内按需读)。
_USAGE_RECONCILE_LEDGER = "context/learning/usage_reconcile.jsonl"


def usage_reconcile_lint(scan_root, ledger_path=None) -> list[dict]:
    """token 真计量 × 配置回显对账 lint(Wave11 B4;design:
    2026-08-05-wave11-ruler-config-l4concurrency-skills-design.md §B4)。

    **时序如实声明(勿"修"成看似当日闭环)**:GATE4/self_review 跑在 `usage_harvest`
    (进而 `autoresearch.trace.usage_reconcile`)**之前**——本次跑动自己的
    `_usage_reconcile.json` 此刻还不存在。本 check 读的是 `scan_root` 下**最近一份既有**
    结果(`context/scan/*/_usage_reconcile.json` 里 dirname 最大、且该文件确实存在的一份,
    通常 = 上一次 run 的结论),**不是「今天」的结论**——今天的结论由 CP7 第五条命令
    (`usage_reconcile <date>`)跑完直接打给人看,不经这里转手。

    `ok=false`(最近一份对账有 mismatch/wire_break)→ 记一条 warn「配置-实测不符(<date>)」;
    若 streak 账本(缺省 `context/learning/usage_reconcile.jsonl`,可用 `ledger_path` 覆盖,
    测试用)**末两行皆 `ok=false`** → 该条 severity 升 `fail`(连续两日 = 系统性偏差,不是
    单次抖动;承既有"warn 升 binding"惯例,不新开一套语义)。

    presence-gated:两份产物(最近一份 `_usage_reconcile.json` / streak 账本)缺一个或都缺
    → 按"还没有历史可查"处理,返回 `[]`,**绝不抛异常**——本函数只读不写,坏文件/缺文件
    与"从未跑过"同等对待(与本文件其余 lint 函数的容错惯例一致)。
    """
    import contextlib
    import json
    from pathlib import Path

    scan_root = Path(scan_root)
    ledger = Path(ledger_path) if ledger_path else Path(_USAGE_RECONCILE_LEDGER)
    out: list[dict] = []

    latest, latest_date = None, None
    with contextlib.suppress(Exception):
        for d in sorted((p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"),
                        reverse=True):
            f = d / "_usage_reconcile.json"
            if f.exists():
                latest = json.loads(f.read_text(encoding="utf-8"))
                latest_date = latest.get("date", d.name)
                break

    if not latest or latest.get("ok", True):
        return out                          # presence-gated,或最近一份本就干净 → 无话可说

    severity = "warn"
    streak = False
    with contextlib.suppress(Exception):
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        last_two = lines[-2:]
        if len(last_two) == 2 and all(json.loads(ln).get("ok") is False for ln in last_two):
            severity, streak = "fail", True

    out.append({
        "check": "usage_reconcile·配置-实测不符",
        "severity": severity,
        "detail": f"配置-实测不符({latest_date})"
                  + ("——streak 账本连续两日 ok=false,系统性偏差,非单次抖动" if streak else
                     "(读的是最近一份既有结果,非今日结论——见 usage_reconcile 报表头时序声明)"),
        "code": None,
    })
    return out


# ── brief 一致性 lint(Wave12 T27 / 批C C3)──────────────────────────────────────
#
# **它对账的到底是什么**:brief.md 是确定性模板产物,`_brief_sources.json` 是同一次生成
# 附出来的边表(逐行 field/value/file/locator/text)。这条 lint 做三件互不重叠的事:
#   ① 边表**重算**:从白名单输入重新跑一遍 `brief.build`,与盘上边表逐行比 —— 抓「输入变了
#      但产物没重生成」的过期;
#   ② 正文**锚在**:每行的 `text`(该数字在 brief 里的完整渲染片段)必须真出现在 brief.md
#      里 —— 抓「brief 被手改过」(篡改一个评级/基准读数即失配);
#   ③ 跨层**同源**:四个决策字段的同一片段必须也出现在 summary.md 的 🧭 仪表盘块里 ——
#      抓「两层报告各说各话」。
# 三件事都不靠「lint 自己再渲染一遍然后跟自己比」,那种写法只证明渲染器等于自己。
#
# **为什么不挂在 `_self_review_banner` 里**:那个函数在 `build_summary` **内部**跑,而此刻
# brief.md 与 `_relative_buy_decision.json` 都还不存在(见 report_sections 的 DASHBOARD 注)。
# 接线点在 `publisher.run` 收尾,与 brief 落盘同一处。
_BRIEF_DECISION_FIELDS = ("buys.production_n", "relative.code", "relative.rank",
                          "relative.market_n")


def brief_lint(report_dir, scan_dir=None) -> list[dict]:
    """`brief.md` 一致性 lint(零 LLM;坏输入只报条目,**绝不抛**)。

    `report_dir` = `reports/scan/<run>/`(brief.md + summary.md);
    `scan_dir` = `context/scan/<date>/`(`_brief_sources.json` + `_relative_buy_decision.json`),
    缺省 = `report_dir`(便于对同一目录的合成夹具跑)。返回 `[{check,severity,detail,code}]`。

    ⑤ **BUY≥1 契约只在 active 模式生效**:`mode` 从 `_relative_buy_decision.json` 读;影子期
    该检查跳过,但**出一条 info 留痕**——静默跳过会让「这道门什么时候开始管事」不可查
    (recalibrate 空转 2 周的同族教训)。
    """
    import contextlib
    import json
    from pathlib import Path

    out: list[dict] = []

    def add(check, sev, detail, code=None):
        out.append({"check": check, "severity": sev, "detail": detail, "code": code})

    report = Path(report_dir)
    scan = Path(scan_dir) if scan_dir is not None else report
    try:
        from autoresearch.scan import brief as _brief
    except Exception as exc:  # noqa: BLE001 — 模块都装不上就只报一条,不炸
        add("brief·lint不可用", "warn", f"brief 模块不可导入:{type(exc).__name__}")
        return out

    # ① 在场 + ② 预算
    path = report / _brief.BRIEF_FILENAME
    if not path.exists():
        add("brief·缺失", "fail",
            f"{path} 不存在 —— 报告双层的速读层没落盘(publisher 接线断了?)")
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        add("brief·缺失", "fail", f"{path} 读不出:{type(exc).__name__}")
        return out
    n_bytes = len(text.encode("utf-8"))
    if n_bytes > _brief.MAX_BYTES:
        add("brief·超预算", "fail",
            f"{n_bytes}B > 硬预算 {_brief.MAX_BYTES}B —— 速读层撑破了就不再是速读层")

    # ③ sources 边表:重算 + 锚在 + 白名单
    payload = None
    with contextlib.suppress(Exception):
        payload = json.loads((scan / _brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        add("brief·边表缺失", "fail",
            f"{scan / _brief.SOURCES_FILENAME} 缺失/损坏 —— 无边表则 brief 里的数字无法对账")
        rows: list[dict] = []
    else:
        rows = [r for r in payload["rows"] if isinstance(r, dict)]
        fresh = None
        with contextlib.suppress(Exception):
            fresh = _brief.build(scan, run_folder=payload.get("run_folder") or "")
        if fresh is not None:
            stale = [r["field"] for r, f in zip(rows, fresh["sources"], strict=False)
                     if r != f]
            if len(rows) != len(fresh["sources"]) or stale:
                add("brief·边表过期", "fail",
                    f"边表与白名单输入重算结果不符({len(rows)} vs "
                    f"{len(fresh['sources'])} 行;首个差异 {stale[:3] or '行数'})"
                    " —— 输入变了但产物没重生成")
        bad_anchor = [r.get("field") for r in rows if str(r.get("text") or "") not in text]
        if bad_anchor:
            add("brief·数字对账", "fail",
                f"{len(bad_anchor)} 个字段的渲染片段不在 brief 正文里:"
                f"{'、'.join(str(f) for f in bad_anchor[:5])}"
                " —— brief 被手改过,或渲染与边表脱钩")
        outside = sorted({str(r.get("file")) for r in rows
                          if r.get("file") not in _brief.INPUT_WHITELIST})
        if outside:
            add("brief·白名单外取数", "fail",
                f"{outside} 不在 `brief.INPUT_WHITELIST` —— 生成器禁读 details 全文与 trace 大文件")

    # ④ brief ↔ summary(四个决策字段的**同一片段**必须两边都在)
    summary_path = report / "summary.md"
    summary = ""
    with contextlib.suppress(Exception):
        summary = summary_path.read_text(encoding="utf-8")
    if not summary:
        add("brief↔summary不一致", "fail",
            f"{summary_path} 缺失/空 —— 无法验证两层报告说的是同一件事")
    else:
        by_field = {str(r.get("field")): r for r in rows}
        missing = [f for f in _BRIEF_DECISION_FIELDS
                   if f in by_field and str(by_field[f].get("text") or "") not in summary]
        if missing:
            add("brief↔summary不一致", "fail",
                f"决策字段在 summary 的 🧭 仪表盘块里对不上:{'、'.join(missing)}"
                " —— 两层报告的 BUY 数/code/basis/基准读数必须同源同值")

    # ⑤ active 期 BUY 契约(影子期跳过并留痕)
    decision = None
    with contextlib.suppress(Exception):
        decision = json.loads((scan / _brief.DECISION_FILENAME).read_text(encoding="utf-8"))
    mode = str((decision or {}).get("mode") or "ABSENT")
    if mode != "active":
        add("brief·BUY契约(active 期)", "info",
            f"mode={mode} —— 非 active,「成功 run 必须至少 1 只 BUY」与「BLOCKED 不得渲染成"
            "成功」两条跳过(影子期只观察,不拦发布);活体切换后本条自动生效")
    else:
        blocked = bool((decision or {}).get("blocked"))
        n_buys = len((decision or {}).get("buys") or [])
        if blocked:
            if "BLOCKED" not in text:
                add("brief·BUY契约(active 期)", "fail",
                    "决策文档 blocked=true 但 brief 没渲染 BLOCKED —— 故障被写成了成功 run")
        elif n_buys < 1:
            add("brief·BUY契约(active 期)", "fail",
                f"成功 run 的 BUY_n={n_buys}<1 —— active 期每个成功交易日至少一只(E6 裁定)")
    return out


def append_gate_fires(scan_dir, rows: list[dict], date: str) -> int:
    """把额外 lint 条目**追加**进 `gate_fires.csv`(不覆写 —— `dump_gate_fires` 是覆写口径,
    而本函数在它之后跑)。表头缺失时补写;IO 失败返回 0,不抛。"""
    import csv
    from pathlib import Path

    if not rows:
        return 0
    path = Path(scan_dir) / "gate_fires.csv"
    fields = ["date", "code", "check", "severity", "detail"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            for row in rows:
                writer.writerow({"date": date, "code": row.get("code") or "",
                                 "check": row.get("check", ""),
                                 "severity": row.get("severity", ""),
                                 "detail": row.get("detail", "")})
    except OSError:
        return 0
    return len(rows)


def dump_gate_fires(scan_dir, result: dict, date: str):
    """R3·门审计地基:review 结果幂等落 <scan_dir>/gate_fires.csv(每次 assemble 覆写)。

    无 failures 也写表头(区分"没拦"与"没跑");retro 侧 join fwd 度量"被拦的后来怎么走"。
    """
    import csv
    from pathlib import Path

    p = Path(scan_dir) / "gate_fires.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        for x in result.get("failures", []):
            w.writerow({"date": date, "code": x.get("code") or "", "check": x.get("check", ""),
                        "severity": x.get("severity", ""), "detail": x.get("detail", "")})
    return p


def dump_ow_gate_fires(scan_dir) -> int:
    """逐满卡解析 OW 三门(主力真在/业绩真兑现/估值不透支)失守 → gate_fires.csv 追加 binding 行。

    R3 门审计地基的姊妹函数:dump_gate_fires 记 self_review 硬门,本函数记卡文里『OW三门…』段的
    结构化失守——此前只在卡片散文里留痕,从没进过 gate_fires.csv,gate_ledger 拿不到这三门的账。
    (date,check,code) 幂等(可重复调用不重复落账);presence-gated(缺 details/ 或无满卡 → 0 行,不炸)。
    返回本次新增行数。
    """
    from pathlib import Path

    import pandas as pd

    from autoresearch.scan.l4.parsers import gate_status

    scan_dir = Path(scan_dir)
    date = scan_dir.name
    fp = scan_dir / "gate_fires.csv"
    old = pd.read_csv(fp, dtype=str) if fp.exists() else pd.DataFrame(columns=["date", "check", "code", "level"])
    seen = {(r["date"], r["check"], r["code"]) for _, r in old.iterrows()}
    rows = []
    for card in sorted((scan_dir / "details").glob("*.md")):
        gates = gate_status(card.read_text(encoding="utf-8")) or {}
        code = card.stem.split(".")[0]
        for gate, failed in gates.items():
            key = (date, f"OW三门·{gate}", code)
            if failed and key not in seen:
                rows.append(dict(zip(("date", "check", "code"), key, strict=True), level="binding"))
                seen.add(key)   # 同次调用内也去重(两张卡巧合同 code 时防重复行,不止防跨调用重跑)
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(fp, index=False)
    return len(rows)


def render_banner(result: dict) -> str:
    """自检结果 → 报告顶部 banner(有 fail 醒目拦截,有 warn 提示)。无问题返回空串。"""
    if not result["failures"]:
        return ""
    icon = "🛑 自检未通过(发布前须先修根因)" if result["n_fail"] else "⚠️ 自检提示"
    lines = [f"> {icon} — fail {result['n_fail']} / warn {result['n_warn']}"]
    for x in result["failures"]:
        mark = "🛑" if x["severity"] == "fail" else "⚠️"
        lines.append(f"> {mark} **{x['check']}**:{x['detail']}")
    return "\n".join(lines) + "\n"


def _selftest() -> int:
    fails: list[str] = []

    # 干净盘 → ok
    clean = {"finalists": [{"code": "600519", "rating": "Overweight", "composite": 70,
                            "winner_rate": 40, "pct_60d": 12, "rsi6": 55, "sector": "白酒"},
                           {"code": "000001", "rating": "Hold", "composite": 55, "sector": "银行"}],
             "n_cards_expected": 2, "n_cards_present": 2, "summary_text": "DCF 高估但 LBO 仍赚 IRR"}
    r = review(clean)
    if not r["ok"] or r["n_fail"]:
        fails.append(f"干净盘应通过: {r}")

    # 获利盘满的买单 → fail
    r2 = review({"finalists": [{"code": "300001", "rating": "Buy", "composite": 60,
                               "winner_rate": 95, "sector": "电子"}],
                 "n_cards_expected": 1, "n_cards_present": 1})
    if r2["ok"] or not any(x["check"] == "经验红线·获利盘满" for x in r2["failures"]):
        fails.append(f"winner_rate 满买单应 fail: {r2}")
    # override 豁免
    r2b = review({"finalists": [{"code": "300001", "rating": "Buy", "winner_rate": 95,
                                "override": True}], "n_cards_expected": 1, "n_cards_present": 1})
    if not r2b["ok"]:
        fails.append("override 应豁免经验红线")

    # 覆盖率不足 → fail
    r3 = review({"finalists": [], "n_cards_expected": 30, "n_cards_present": 10})
    if r3["ok"] or not any(x["check"] == "覆盖率不足" for x in r3["failures"]):
        fails.append(f"覆盖 10/30 应 fail: {r3}")

    # 空泛话术 + 行业集中 → warn(不致命)
    r4 = review({"finalists": [{"code": "1", "rating": "Buy", "composite": 60, "sector": "电子"},
                               {"code": "2", "rating": "Buy", "composite": 60, "sector": "电子"}],
                 "n_cards_expected": 2, "n_cards_present": 2, "summary_text": "基本面良好,值得关注"})
    if not any(x["check"] == "空泛话术" for x in r4["failures"]):
        fails.append("空泛话术应被抓")
    if not any(x["check"] == "行业过度集中" for x in r4["failures"]):
        fails.append("行业集中应被抓")
    if r4["n_fail"]:
        fails.append("空泛/集中只应是 warn 非 fail")

    # 结构化 guard(lesson 带 field/op/value)→ fail
    r5 = review({"finalists": [{"code": "9", "rating": "Buy", "winner_rate": 92}],
                 "n_cards_expected": 1, "n_cards_present": 1,
                 "lessons": [{"id": "ls_wr", "guard": {"field": "winner_rate", "op": ">", "value": 90}}]})
    if r5["ok"] or not any("违背经验" in x["check"] for x in r5["failures"]):
        fails.append(f"结构化 guard 应触发 fail: {r5}")

    # 评级超 rubric 建议 → warn(card 评分卡只支持 Hold 却给了 OW);偏离说明 → 豁免
    r6 = review({"finalists": [{"code": "7", "rating": "Overweight", "composite": 50,
                                "rubric_suggest": "Hold"}], "n_cards_expected": 1, "n_cards_present": 1})
    if not any(x["check"] == "评级超rubric" for x in r6["failures"]) or r6["n_fail"]:
        fails.append(f"评级超 rubric 应 warn(非 fail): {r6}")
    r6b = review({"finalists": [{"code": "7", "rating": "Overweight", "rubric_suggest": "Hold",
                                 "rubric_dev": True}], "n_cards_expected": 1, "n_cards_present": 1})
    if any(x["check"] == "评级超rubric" for x in r6b["failures"]):
        fails.append("偏离说明应豁免评级超rubric")

    # banner 渲染(r=干净结果 banner 空;r3=fail 含 🛑)
    if "🛑" not in render_banner(r3) or render_banner(r) != "":
        fails.append("banner 渲染错")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  覆盖率/经验红线(获利盘满·override)/评级矛盾/行业集中/空泛话术/结构化guard"
          "/评级超rubric(C)/banner 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
