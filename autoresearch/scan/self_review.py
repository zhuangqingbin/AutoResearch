#!/usr/bin/env python3
"""发布前机械自检硬门(UZI「self-review gate」本地版)· 纯函数可自测,零 LLM。

把"评级-因子矛盾 / 覆盖不足 / 行业过度集中 / 空泛话术 / 报告说假话"做成发布前的机械检查:
**有 fail 就不该直接发,先修根因**(assemble 把 fail 顶到报告最前作 banner;skill 据此先改)。
GATE4 的判据真身 —— `gate_fires.csv` 里任意一行 `severity=fail` 即拦本趟(见 `scan/gates.py`)。

2026-08-21(用户裁定「整个 learning 层退役」)本模块从 `autoresearch/learning/` 搬到
`autoresearch/scan/`:它检的是**发布物自身对不对**,不是"从历史里学到了什么",与闭环无关。
同批摘掉唯一一条闭环腿 —— 原 check#5「违背经验」读 `feedback_store` 的结构化 guard,
随 lessons 一起退役;`review()` 不再接受 `lessons` 参数。check#2 的两条经验红线
(winner_rate>88 抛压 / 60日涨幅+RSI6 过热)是**写死的常量阈值**,不读任何账本,故保留。

用法:uv run --no-sync python -m autoresearch.scan.self_review --selftest
"""
from __future__ import annotations

import re
import sys
from collections import Counter

from autoresearch.common import workspace as ws

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



#: capsule `web_budget.json`(落点见 `_WEB_BUDGET_RELS`)的 schema(外源扩面设计稿 §6.2)。**严格按这 11 个键读,
#: 缺任一键 = UNMEASURED**(不是 0、更不是「未超限」)—— 「transcript 未绑定 / payload 解析
#: 不动 → UNMEASURED」是那份稿子写死的判据,而 0 会被人读成「一次都没查」。
WEB_BUDGET_KEYS = ("measurement", "tool_calls", "search_queries", "fetched_urls",
                   "failed_units", "duplicate_urls", "wall_s", "cap_unit", "cap",
                   "cap_enforcement", "self_report_delta")


def read_web_budget(path) -> tuple[dict | None, str]:
    """读 §6.2 的 `web_budget.json` → `(obj, why)`;`obj is None` = 不可用,`why` 说明原因。

    一切异常路径都落到「不可用」,**绝不抛**:量不到是法证诊断,不是发布故障。
    """
    import json
    from pathlib import Path

    fp = Path(path)
    if not fp.exists():
        return None, "web_budget.json 缺席"
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, "web_budget.json 读不动/非法 JSON"
    if not isinstance(obj, dict):
        return None, "web_budget.json 顶层不是对象"
    missing = [k for k in WEB_BUDGET_KEYS if k not in obj]
    if missing:
        return None, f"web_budget.json 缺键 {missing[:3]}"
    return obj, ""


#: capsule 里 `web_budget.json` 的**两个**落点,按优先序探。
#: ① `capsule/usage/web_budget.json` —— finalize 的 D-5 materializer 接线后的落点
#:    (`docs/superpowers/plans/2026-08-29-full-coverage-p0-p1.md` Task 1 Interfaces 逐字);
#: ② `capsule/lineage/web_budget.json` —— `trace/web_budget.materialize_web_budget` 今天
#:    自己写的那个(`root / "lineage" / BUDGET_NAME`)。
#: 只探一个 = 探错了就等于没接线,而「接好了但永远走不到」正是本条要修的病本身。
_WEB_BUDGET_RELS = ("capsule/usage/web_budget.json", "capsule/lineage/web_budget.json")


def _default_web_budget_path(scan_dir):
    """从 `scan_dir` 反解本 run 的 capsule `web_budget.json`;找不到返回 `None`。

    `workspace.scan_dir(date)` = `context_<engine>/scan_runs/<run_id>/staging/<date>`,而
    capsule 与 staging 同级(`trace/capsule.py` 的 `workspace / "capsule"`)—— 所以本趟的
    run 根就是 `scan_dir` 的祖父目录。已发布/回放布局里 capsule 被拷到 run 目录下,故
    `scan_dir` 自身与父目录也各探一次。

    **只认入参反解出来的那趟,不从 `AUTORESEARCH_RUN_ID` 直取**:环境里挂着的 run 与手上
    这个 scan_dir 可以不是同一趟(离线复盘 / 回放 / 补跑),env 直取会把别人的预算当成本趟
    的真值 —— 那比自报还糟(自报至少是本趟的谎)。没有 run 分区的旧布局
    (`context_<engine>/scan/<date>`,压根没有 capsule)自然一个都不中 → `None` → 调用方
    逐字节回落自报路。
    """
    from pathlib import Path

    d = Path(scan_dir)
    for root in (d, *list(d.parents)[:2]):
        for rel in _WEB_BUDGET_RELS:
            candidate = root / rel
            try:
                if candidate.is_file():
                    return candidate
            except OSError:                 # 路径太长/权限 —— 量不到就当没有,绝不抛
                continue
    return None


def intel_query_cap_lint(scan_dir, cap: int = 15, web_budget_path=None) -> list[dict]:
    """情报查询数 vs 配置 cap 对账(product_shape_lint 探针 10 的素材)。

    `l4-intel` 的声明行本来就写「网查 N 条」,但全仓此前**没有任何消费者**读它 ——
    2026-07-24 实测 11 稿自报 18/18/17/15/20/26/23/16/17/21/25(cap=15)→ **10 只超限**,
    最高 26 条 = cap 的 173%,而限频「形同虚设」这件事只在 pr_20260714_007 里挂着没人验。

    ## 两条路(外源扩面设计稿 §6.2)

    - `web_budget_path=None`(默认)= 先 `_default_web_budget_path(scan_dir)` 自己找本趟
      capsule 的真身(P0·T4:生产从来不传路径,于是真值路建好了两周没走过一次,而自报正是
      2026-08-26 八稿超限 22–37 条 vs cap 20 没被发现的原因);**找不到才**回落 **旧自报路**,
      逐字节不变:返回逐码 `{"code", "claimed", "cap"}`;`claimed=None` = 稿里根本没自报,
      **同样上报** —— 缺字段是弱证据,不得以缺推断合规。
    - 给了路径(显式优先)= **真值路**:capsule 的 `web_budget.json` 是权威(一行 tool call ≠ 一次
      查询,batch 里可能有 N 条 query),自报值降为诊断项 `self_report_delta`。返回**至多
      一行** run 级条目(`code=None`):
      * `kind="measured"` = 真值超 cap(`{"used","unit","cap","self_report_delta",…}`);
        未超 → `[]`(不出条)。
      * `kind="unmeasured"` = `measurement != MEASURED` / 文件缺席 / 缺键 → **既不判超限也
        不判合规**,由调用方落 `intel_budget_unmeasured` warn:**量不到 ≠ 没超**。

    无 intel 稿 → `[]`(presence-gated;两条路都是 —— 情报站没跑就没有它的预算要对账)。
    """
    import re
    from pathlib import Path

    d = Path(scan_dir)
    out: list[dict] = []
    claimed_all: list[int] = []
    n_files = 0
    for p in sorted(d.glob("_l4_intel_*.md")):
        n_files += 1
        code = p.stem.replace("_l4_intel_", "")
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 读不了按未自报处理,不静默跳过
            out.append({"code": code, "claimed": None, "cap": cap})
            continue
        m = re.search(r"网查\s*(\d+)\s*条", text)
        if m is not None and "〔已裁·cap" in text:
            claimed_all.append(int(m.group(1)))   # 计入总数,但不再当指令级违规上报
            continue
        if m is None:
            out.append({"code": code, "claimed": None, "cap": cap})
        else:
            claimed_all.append(int(m.group(1)))
            if int(m.group(1)) > cap:
                out.append({"code": code, "claimed": int(m.group(1)), "cap": cap})
    if web_budget_path is None:
        web_budget_path = _default_web_budget_path(d)   # 生产不传路径也要走真值路
    if web_budget_path is None:
        return out                                  # 无 capsule → 旧自报路:逐字节不变
    if n_files == 0:
        return []                                   # presence-gated
    obj, why = read_web_budget(web_budget_path)
    self_total = sum(claimed_all) if claimed_all else None
    base = {"code": None, "unit": "search_queries", "cap": cap, "used": None,
            "self_report_total": self_total, "self_report_delta": None,
            "n_self_reported": len(claimed_all), "n_intel_files": n_files}
    if obj is None or str(obj.get("measurement")) != "MEASURED":
        reason = why or f"measurement={obj.get('measurement')!r}"
        return [{**base, "kind": "unmeasured", "measurement": "UNMEASURED", "reason": reason}]
    unit = str(obj.get("cap_unit") or "search_queries")
    used = obj.get(unit) if unit in obj else obj.get("search_queries")
    cap_raw = obj.get("cap")
    cap_val = int(cap_raw) if isinstance(cap_raw, (int, float)) and cap_raw > 0 else cap
    if not isinstance(used, (int, float)):
        return [{**base, "kind": "unmeasured", "measurement": "UNMEASURED", "unit": unit,
                 "cap": cap_val, "reason": f"cap_unit={unit} 真值缺失"}]
    delta = obj.get("self_report_delta")
    if not isinstance(delta, (int, float)):
        delta = None if self_total is None else self_total - used
    row = {**base, "kind": "measured", "measurement": "MEASURED", "unit": unit,
           "cap": cap_val, "used": used, "self_report_delta": delta,
           "cap_enforcement": str(obj.get("cap_enforcement") or "")}
    return [row] if used > cap_val else []


def review(ctx: dict) -> dict:
    """机械自检。ctx: {finalists:[{code,rating,composite,winner_rate,pct_60d,rsi6,sector,override?}],
    n_cards_expected, n_cards_present, summary_text, 阈值可选}。

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
        # ⚠️ E3b(2026-08-19)· 恢复前必读:`flow.buys_n` 在 **active 期是 `None`**,并由
        # `flow.buys_n_source` 标明原因(决策文件比 `build_summary` 晚一站写盘,此刻真买单数
        # 不可知)——直接恢复上面三行,active 期这条 lint 会静默永不触发(「记进 lessons ≠
        # 会生效」同族)。要恢复就得先把它挪到 `brief_lint` 那一层(决策文件已在盘)。
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
    tests/scan/test_product_shape_lint.py::test_intel_pretrim_archive_not_counted)。
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


# 日历天口径(不是交易日):">1 周" 取 7 天,与契约里给 agent 的说法逐字一致。
_INTEL_STALE_DAYS = 7
_INTEL_CATALYST_WIN = "催化挂"

# ── 时效契约 v2(外源扩面设计稿 §6.4):三套窗按**契约名**参数化 ────────────────────
# 各时效窗允许的日期跨度(天):宽松取并集 —— T0 与 24h 无法从**日期**区分(同一天盘后
# 与当天白天都是 gap 0),探针不该假装分得清;分不清的地方就不报。
#   intel_v1        = 现行微观 lite:T0 / 24h / 背景;
#   intel_v2_full   = 微观 full · 中观 full:24h / 本周 / 本月 / 背景;
#   intel_v2_macro  = 宏观 full:48h / 本周 / 本月 / 背景。
_INTEL_WINDOW_SPANS: dict[str, dict[str, tuple[int, int]]] = {
    "intel_v1": {"T0": (0, 0), "24h": (0, 1), "背景": (1, _INTEL_STALE_DAYS)},
    "intel_v2_full": {"24h": (0, 1), "本周": (0, 7), "本月": (0, 31), "背景": (1, 60)},
    "intel_v2_macro": {"48h": (0, 2), "本周": (0, 7), "本月": (0, 31), "背景": (1, 60)},
}
#: 兼容别名(旧调用方 / 旧测试读的是这个名字):= intel_v1 的窗表。
_INTEL_WINDOW_SPAN = _INTEL_WINDOW_SPANS["intel_v1"]
#: 「旧契约稿整份跳过」的识别词表 = 该契约的窗名 + 催化挂(催化挂三套契约共有,不衰减)。
_INTEL_CONTRACT_WINDOWS: dict[str, tuple[str, ...]] = {
    name: (*spans, _INTEL_CATALYST_WIN) for name, spans in _INTEL_WINDOW_SPANS.items()}
_INTEL_WINDOWS = _INTEL_CONTRACT_WINDOWS["intel_v1"]     # ("T0","24h","背景","催化挂")
#: 「超出这个天数还带非零净分」= 未按时效衰减。v1 沿用 7 天(>1周);v2 三窗把「本月」放到
#: 31 天,故越过本月才算未衰减 —— **不是**把 v1 的尺硬套到 v2 稿上(量错对象类)。
#: 注:§6.4 的净分系数表(1/1/0.5/0)本 lint **不逐档强制**,只守「越过月窗必须归零」这条边,
#: 与 v1 只守「越过周窗必须归零」同形。
_INTEL_CONTRACT_STALE_DAYS: dict[str, int] = {
    "intel_v1": _INTEL_STALE_DAYS, "intel_v2_full": 31, "intel_v2_macro": 31}
#: 播报用的窗名(保住 v1 的历史文案逐字节不变)。
_INTEL_STALE_LABEL: dict[str, str] = {
    "intel_v1": ">1周", "intel_v2_full": ">1月", "intel_v2_macro": ">1月"}
#: 只有这些窗**声称一个精确时点**;`DATE_ONLY`(行里没有时刻)不得自称它们(§6.4)。
_INTEL_EXACT_WINDOWS = ("T0",)


def intel_recency_lint(scan_dir, date_str: str, contract: str = "intel_v1") -> list[dict]:
    """intel 时效窗机检(advisory;Wave7 批 N §4.3;时效契约 v2 见外源扩面稿 §6.4)。四条:

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

    4. **DATE_ONLY 自称 T0**:v2 行首格是 `日期时间 / time_quality`;`time_quality=DATE_ONLY`
       (整行没有时刻)却把窗标成 `T0` → warn。T0 = 「收盘后到跑报之间」这一个精确时点,
       只有日期的行**证明不了**自己落在那个窗里,不得伪装(§6.4)。行里既无时刻也无
       `time_quality` 标记(= v1 存量稿的写法)→ 判不出质量,**不报**(分不清的地方就不报)。

    `contract` ∈ `intel_v1`(默认,现行微观 lite:T0/24h/背景)/ `intel_v2_full`(24h/本周/
    本月/背景)/ `intel_v2_macro`(48h/本周/本月/背景)。默认行为逐字节不变;未知契约名
    **回落 v1 并落一条 `intel_contract_unknown` warn**(降级不等于消音:静默换尺 = 量错对象)。

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

    if contract not in _INTEL_WINDOW_SPANS:
        add("intel_contract_unknown", "warn",
            f"未知时效契约 `{contract}` —— 已回落 `intel_v1` 的窗表;"
            f"可选:{'/'.join(_INTEL_WINDOW_SPANS)}")
        contract = "intel_v1"
    spans = _INTEL_WINDOW_SPANS[contract]
    windows = _INTEL_CONTRACT_WINDOWS[contract]
    stale_days = _INTEL_CONTRACT_STALE_DAYS[contract]
    stale_label = _INTEL_STALE_LABEL[contract]

    try:
        as_of = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return out

    # 首格容错到 v2 的 `日期 时刻 / time_quality`(§6.4);v1 的纯日期格是它的子集,解析结果不变。
    row_re = re.compile(
        r"\|\s*(\d{4}-\d{2}-\d{2})"                    # 1 日期
        r"(?:[ T](\d{1,2}:\d{2}(?::\d{2})?))?"           # 2 时刻(可选)
        r"\s*(?:/\s*([A-Za-z_]+))?\s*"                   # 3 time_quality(可选)
        r"\|\s*([^|]*?)\s*\|(.*)\|\s*([+-]?\d+(?:\.\d+)?)\s*\|\s*$")
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
                # 显式 time_quality 优先;没标记但有时刻 = EXACT;都没有 = 判不出("")。
                quality = (m.group(3) or "").upper() or ("EXACT" if m.group(2) else "")
                rows.append((m.group(1), quality, m.group(4).strip(), float(m.group(6))))
        if not any(w in windows for _, _, w, _ in rows):
            continue                      # 旧契约稿(Wave7 前)→ 整份跳过,不报
        bad_win, stale, date_only = [], [], []
        for d, quality, win, score in rows:
            try:
                gap = (as_of - datetime.strptime(d, "%Y-%m-%d")).days
            except ValueError:
                continue
            if quality == "DATE_ONLY" and win in _INTEL_EXACT_WINDOWS:
                date_only.append(f"{d}({win})")
            if gap < 0:
                continue                  # 前视由 intel_future_dates_lint 管,不重复报
            span = spans.get(win)
            if span and not (span[0] <= gap <= span[1]):
                bad_win.append(f"{d}({win},实距 {gap}d)")
            if gap > stale_days and win != _INTEL_CATALYST_WIN and score != 0:
                stale.append(f"{d}({score:+g})")
        if bad_win:
            add("intel_window_mismatch", "warn",
                f"{p.name} 时效窗与日期不符:{'、'.join(bad_win[:3])}", code=code)
        if stale:
            add("intel_stale_score", "warn",
                f"{p.name} {stale_label}事件净分未衰减到 0(且未标 催化挂):"
                f"{'、'.join(stale[:3])}", code=code)
        if date_only:
            add("intel_window_date_only_t0", "warn",
                f"{p.name} DATE_ONLY 行自称 {_INTEL_EXACT_WINDOWS[0]}(无时刻,证不了落在该窗):"
                f"{'、'.join(date_only[:3])}", code=code)
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


#: skill 文档里的 agent 档位字面量判据(Wave12-T33)。只认 `Agent(` 调用形态里的
#: `model=`/`effort=` 字面量 —— 散文里提一句 "effort" 不算违规,写成调用形态才算。
_SKILL_LITERAL_PATTERN = r"Agent\s*\([^)\n]*\b(?:model|effort)\s*=\s*['\"]"
#: 合法写法的标记词(同一行任一在场即放行)。**先给合法情形一个标记,再谈加严检查**
#: ——否则"这里就是要举个调用的例子"这种正当写法会被天天判违规(2026-07-27 家训)。
_SKILL_LITERAL_OK_MARKS = ("档位见 scan_config", "仅作示意", "测试 fixture",
                           "lint-exempt", "沿革", "历史写法", "已退役")


def _skill_agent_literal_lint(base) -> list[dict]:
    """`.claude/skills/**/*.md` 的 `Agent(model=…)` 字面量探针(Wave12-T33)。

    presence-gated:目录不存在 / 坏文件 → 静默跳过,绝不抛异常。
    """
    from pathlib import Path

    skills = Path(base) / "skills"
    out: list[dict] = []
    if not skills.is_dir():
        return out
    pat = re.compile(_SKILL_LITERAL_PATTERN)
    for p in sorted(skills.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if not pat.search(line):
                continue
            if any(mark in line for mark in _SKILL_LITERAL_OK_MARKS):
                continue                                  # 带注记的合法写法
            out.append({
                "check": "产物形状·skill内联字面量",
                "severity": "fail",
                "detail": f"{p.relative_to(Path(base).parent) if Path(base).name == '.claude' else p}"
                          f":{i} skill 文档里写死 agent model/effort 字面量 —— "
                          f"单一事实源是 scan_config.agents(见 user_config._AGENT_ROLES);"
                          f"确需举例请在同一行注明 {list(_SKILL_LITERAL_OK_MARKS)[:3]} 之一",
                "code": None,
            })
    return out


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

    没有 `const AGENT_DEFAULTS = {` 表的 workflow 天然不受①约束——presence-gated 跳过,
    不是本规则的检查对象,防止跟另一套架构打架。(Wave12-T33 起 t1-review.js 也有了这张表,
    所以现在四个 workflow 全在网内。)

    **Wave12-T33 扩面**:`.claude/skills/**/*.md` 里的 `Agent(model=…)`/`Agent(effort=…)`
    字面量同样 fail。理由:skill 文档是**给 Claude 读的活指令**,写死一个档位等于在
    单一事实源之外开第二个口子(`lite-playbook.md` 那处写着 `Agent(model='opus')`,而
    生产早就走 `l4-stock.js` + `scan_config.agents.l4_card`,文档在按过时事实指挥人)。
    合法情形给标记不给例外:同一行带 `_SKILL_LITERAL_OK_MARKS` 任一注记即放行
    (2026-07-27 家训:修法排序 = 补指令 > 给合法情形一个标记 > 才是加严检查)。

    presence-gated:目录不存在、坏文件 → 静默跳过,绝不抛异常。
    """
    import re
    from pathlib import Path

    base = Path(root)
    out: list[dict] = []
    out.extend(_skill_agent_literal_lint(base))
    root = base / "workflows"
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


#: `[执行线]` 两份活文档路径(D8.3⑤;数值真身单源在
#: `contracts.agent_output.EXEC_LINE_MAX_PCT_1D`/`EXEC_LINE_MAX_POS_IN_RANGE`)。
#: **带 `.claude/` 前缀**——这两个字面量已经登记在
#: `contracts.artifacts.NON_ARTIFACT_LITERALS` 里(是散文,不是产物),写成不带
#: 前缀的裸相对路径会被 `test_no_unregistered_artifact_literals` 判成新字面量。
_EXEC_LINE_DOCS = (
    ".claude/skills/stock-research/lite-playbook.md",
    ".claude/agents/l4-card.md",
)


def exec_line_threshold_lint(root=".") -> list[dict]:
    """`[执行线]` 两份活文档里的阈值数字与常量单源对齐(D8.3⑤;防散文漂移)。

    这两行是**给 agent 照抄的固定散文**(playbook / agent def 里都写死了
    `pct_chg <= 3.0` / `pos_in_range < 0.7` 两行,要求"照抄,勿改阈值勿反向"),数字
    真身是 `contracts.agent_output.EXEC_LINE_MAX_PCT_1D` /
    `EXEC_LINE_MAX_POS_IN_RANGE`(`scan.outcome` 的判定常量与它们是**同一个对象**)。
    此前两边各写一份、没有测试对齐——改常量不动文档、或改文档不动常量,都会在这里
    被拦。**只读不改**:本探针从不写这两份文档,数值本身也不在这里定义。

    `root` 是**仓库根**(与 `stale_ruler_lint` 同款惯例),不是 `.claude` 根——
    两个文档字面量已经带了 `.claude/` 前缀。

    pattern 直接复用 `contracts.agent_output.L4_CARD` 契约里 `exec_line_pct` /
    `exec_line_pos` 两个字段的正则,不在这里另写一份——那样又会制造第三处可以
    独立漂移的阈值解析副本。

    presence-gated:文件缺失 / 读不动 / 锚点缺失 → 各自静默跳过(锚点本身在不在,
    是产出契约 lint 管的事,不是本探针的判定对象)。
    """
    from pathlib import Path

    from autoresearch.contracts.agent_output import (
        EXEC_LINE_MAX_PCT_1D,
        EXEC_LINE_MAX_POS_IN_RANGE,
        contract,
    )

    pct_pattern = re.compile(contract("l4-card").field("exec_line_pct").pattern)
    pos_pattern = re.compile(contract("l4-card").field("exec_line_pos").pattern)
    base = Path(root)
    out: list[dict] = []
    for rel in _EXEC_LINE_DOCS:
        p = base / rel
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — presence-gated,坏文件不炸
            continue
        pct_match = pct_pattern.search(text)
        if pct_match is not None and float(pct_match.group(1)) != EXEC_LINE_MAX_PCT_1D:
            out.append({
                "check": "产物形状·执行线阈值散文漂移",
                "severity": "fail",
                "detail": f"{p} 的 [执行线] pct_chg 阈值写成 {pct_match.group(1)} ≠ "
                          f"常量 EXEC_LINE_MAX_PCT_1D={EXEC_LINE_MAX_PCT_1D}",
                "code": None,
            })
        pos_match = pos_pattern.search(text)
        if pos_match is not None and float(pos_match.group(1)) != EXEC_LINE_MAX_POS_IN_RANGE:
            out.append({
                "check": "产物形状·执行线阈值散文漂移",
                "severity": "fail",
                "detail": f"{p} 的 [执行线] pos_in_range 阈值写成 {pos_match.group(1)} ≠ "
                          f"常量 EXEC_LINE_MAX_POS_IN_RANGE={EXEC_LINE_MAX_POS_IN_RANGE}",
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


LIVENESS_CHECK = "通道活性·名义启用实际空召回"
LIVENESS_ESCALATE_STREAK = 3


def _channel_rows(path) -> dict[str, int] | None:
    """L1_channels.csv → {channel: 行数};缺/坏文件 → None。"""
    import contextlib

    import pandas as pd
    with contextlib.suppress(Exception):
        if path.exists():
            df = pd.read_csv(path, dtype={"code": str})
            if "channel" in df.columns:
                return df["channel"].astype(str).value_counts().to_dict()
    return None


def channel_liveness_lint(scan_dir, date_str: str, *, recall_channels=None,
                          history_days: int = LIVENESS_ESCALATE_STREAK) -> list[dict]:
    """启用通道 0 召回探针(2026-08-21 低位转强波 §5.3)。

    `reversal_confirm` 名义启用实际恒空 4 周+无人发现(起爆硬门列从未接入 L1 帧)——这类死法
    没有任何报错,只有 `L1_channels.csv` 里那一路恒 0 行。探针:config `funnel.recall_channels`
    里的每一路在当日 `L1_channels.csv` 计行,0 行 → **warn**(恒 warn:`fail` 会触发 GATE4 阻断
    发布,探针职责是可见性不是停机);连续 ≥`history_days` 个扫描日 0 行 → detail 加 🔴 前缀。
    `recall_channels=None` → 读 config;config 缺/空 → 不知道谁启用,返回 []。全部 presence-gated,
    绝不抛异常。

    同族前科:「自动学习的腿必须有一个会变的量做断言,否则它死了也像活着」(权重自动重标定
    连续 4 次 NO-OP 空转 2 周)。
    """
    from pathlib import Path
    scan_dir = Path(scan_dir)
    if recall_channels is None:
        try:
            from autoresearch.scan.user_config import load_user_config
            recall_channels = (load_user_config().get("funnel") or {}).get("recall_channels")
        except Exception:  # noqa: BLE001 — 配置层故障不挡自检
            recall_channels = None
    if not recall_channels:
        return []
    today = _channel_rows(scan_dir / "L1_channels.csv")
    if today is None:
        return []
    prior_days = sorted((p for p in scan_dir.parent.iterdir()
                         if p.is_dir() and p.name < scan_dir.name), reverse=True)
    out: list[dict] = []
    for ch in recall_channels:
        if int(today.get(ch, 0)) > 0:
            continue
        streak = 1
        for day in prior_days:
            counts = _channel_rows(day / "L1_channels.csv")
            if counts is None or int(counts.get(ch, 0)) > 0:
                break
            streak += 1
        prefix = "🔴" if streak >= history_days else ""
        out.append({"check": LIVENESS_CHECK, "severity": "warn", "code": ch,
                    "detail": f"{prefix}{ch} 当日 L1_channels.csv 0 行(连续 {streak} 个扫描日)"
                              "—— 名义启用实际空召回:列没接上/门写死/取数坏,先查再谈 edge"})
    return out


def product_shape_lint(scan_dir, date_str: str, *,
                       web_budget_path=None) -> list[dict]:
    """产物形状 lint(十四探针,零 LLM;design: 2026-07-13-next-optimization-survey.md 线 C
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
    14. **低位转强·L2 零到货**(warn):`meta.json` 三段计数 `lowturn_full>0 ∧ lowturn_l2==0`
       → 生产者没送到菜单,L3 侧整套空转(2026-08-21 首跑实况)。三键缺 = 特性未启用,不出条。
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
                        # 同 `l4/prompts.py`:重复码会让 to_dict("index") 抛 ValueError,
                        # 而这里外面套着 suppress —— 炸了不会红,只是这条 force_full 检查
                        # 静默失效(「绿灯不等于有灯」)。显式去重,别让它悄悄不干活。
                        l2 = l2.drop_duplicates(subset="code", keep="first")
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
        # 逐字保留(tests/scan/test_product_shape_lint.py::test_anns_expected_info 锁定这个
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
    # 真值路(外源扩面稿 §6.2):capsule 的 `web_budget.json` 是权威(一行 tool call ≠ 一次
    # 查询),自报只剩诊断价值;量不到 → `intel_budget_unmeasured`。**不传 `web_budget_path`
    # 也照样走真值路** —— `intel_query_cap_lint` 会自己按 `_WEB_BUDGET_RELS` 找本趟 capsule
    # (P0·T4:生产真身 report_sections 从来不传路径,这条真值路此前一次都没走到过)。
    for h in intel_query_cap_lint(scan_dir, cap=_cap, web_budget_path=web_budget_path):
        if h.get("kind") == "unmeasured":
            _diag = ("" if h.get("self_report_total") is None
                     else f";自报合计 {h['self_report_total']} 条(仅诊断)")
            add("intel_budget_unmeasured", "warn",
                f"网查预算量不到({h.get('reason') or 'UNMEASURED'})—— **量不到 ≠ 没超**:"
                f"既不判超限、也不判合规{_diag}", code=None)
            continue
        if h.get("kind") == "measured":
            _d = h.get("self_report_delta")
            add("产物形状·intel限频", "warn",
                f"真值 {h['used']} {h['unit']} > cap {h['cap']} —— 取自 capsule "
                f"`web_budget.json`(自报 delta {'—' if _d is None else _d},仅诊断)",
                code=None)
            continue
        _claimed = "未自报查询数" if h["claimed"] is None else f"自报 {h['claimed']} 条"
        add("产物形状·intel限频", "warn",
            f"{_claimed} > cap {h['cap']} —— 限频是指令级、无强制力(pr_20260714_007);"
            f"未自报 = 无法对账,不等于合规",
            code=h["code"])

    # 14) 低位转强·L2 零到货(2026-08-22):三段计数 `meta.json` 的 lowturn_full/l1/l2。
    # 2026-08-21 首跑 120→17→**0**,L3 侧整套(旗列/pass1 强留/守卫⑥/G 条)空转一整天而
    # 无一行日志——「没有生产者的消费者」这种死法不报错,只有一个谁也没在数的数。
    # 三键缺席 = 该特性两把开关全关 → 不出条(不是坏事,是没开)。
    with contextlib.suppress(Exception):
        _meta = json.loads((scan_dir / "meta.json").read_text(encoding="utf-8"))
        if all(k in _meta for k in ("lowturn_full", "lowturn_l1", "lowturn_l2")):
            _f, _l1, _l2 = (_meta.get(k) for k in ("lowturn_full", "lowturn_l1", "lowturn_l2"))
            if _l2 == 0 and isinstance(_f, int) and _f > 0:
                add("产物形状·低位转强L2零到货", "warn",
                    f"全帧 {_f} 只亮旗 → L1 {_l1} → L2 0 —— 生产者没把货送到菜单,"
                    f"L3 旗列/pass1 强留/守卫⑥ 全部空转(2026-08-21 同款)。"
                    f"先查 recall_channels 是否含 lowturn、L2『低位转强』桶 floor 是否被归零")
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
    # 15)执行线阈值散文漂移(D8.3⑤):按**仓库根**(同 14,不是 .claude 根——本探针
    # 的文档字面量自带 `.claude/` 前缀)。
    with contextlib.suppress(Exception):
        out.extend(exec_line_threshold_lint(claude_root.parent))

    # 13) v4 卡契约口径声明缺失(T17):标记行本体是 T24 的事,这里只加检查
    with contextlib.suppress(Exception):
        out.extend(card_v4_marker_lint(scan_dir, date_str))
    return out


# 与 usage_reconcile 产物同形的路径常量(避免两处各写一遍字面量走漂):streak 账本默认路径
# 跟 `autoresearch.trace.usage_reconcile.LEDGER_PATH` 保持同一字符串,但故意不 import 该模块
# 顶层(self_review 是纯 lint 层,不想给它添一条对 trace 包的硬依赖——这里只在函数体内按需读)。
_USAGE_RECONCILE_LEDGER = ws.learning_root() / "usage_reconcile.jsonl"


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
#: **GATE4 语义二分**(B-2,2026-08-09 控制方裁定)—— 判据名 → severity 的**单一事实源**。
#:
#: 病灶:`publisher` 把本 lint 的结果 `append_gate_fires` 进 `gate_fires.csv`,而
#: `scan.gates.gate4` 的判据是「任意一行 `severity=="fail"` 就不过」。于是**一份人类可读
#: 摘要排版超限**(>3,000B)就能毙掉整条约 60 分钟的流水线 —— 本仓有「GATE3 差 16 字节毙
#: 60min 流水线」的疤,同一形状。而本波自己两处写着「失败不阻断发布」(`publisher.run`
#: 的 brief 段注释)、`brief.safe_publish` 刻意吞异常:一边为了不阻断而吞,另一边把吞下去
#: 的结果变成门失败。
#:
#: **分法只有一问:报告是不是在说假话。**
#:   `fail` —— 报告陈述与事实/自身矛盾,硬门必须拦(数字被手改、两层报告各说各话、
#:              数字来自白名单外的源、active 期把 BLOCKED/0 BUY 渲染成成功 run);
#:   `warn` —— 报告**畸形或缺失**,是展示层问题,不该毁掉一次已经跑完的扫描
#:              (brief 没落盘、排版超限、对账夹具缺失或过期)。
#:
#: 一份人类可读摘要排版超限是展示层问题;**报告说假话才是硬门该拦的事**。
#: 降级 ≠ 消音:warn 照样进 `gate_fires.csv`,也照样由 `brief_lint_banner` 播给 CP7。
BRIEF_LINT_SEVERITY = {
    # ── fail:报告在说假话 ──
    "brief·数字对账": "fail",
    "brief↔summary不一致": "fail",
    "brief·白名单外取数": "fail",
    "brief·BUY契约(active 期)": "fail",
    "brief③相对BUY与决策文件不同源": "fail",
    # ── warn:报告畸形/缺失 ──
    "brief·缺失": "warn",
    "brief·超预算": "warn",
    "brief·边表缺失": "warn",
    "brief·边表过期": "warn",
}

_BRIEF_DECISION_FIELDS = ("buys.production_n", "relative.code", "relative.rank",
                          # B-1(2026-08-09):原为 `relative.market_n` —— 那个名字同时
                          # 可以指「L0 过门票」与「全市场可交易」两个人口,brief 已按
                          # `decision_pool_n` / `eval_population` 拆开。跨层比对锚同一段
                          # `text`,取其中一个即可,取的是有数的那个。
                          "relative.decision_pool_n")


def brief_lint(report_dir, scan_dir=None) -> list[dict]:
    """`brief.md` 一致性 lint(零 LLM;坏输入只报条目,**绝不抛**)。

    `report_dir` = `reports/scan/<run>/`(brief.md + summary.md);
    `scan_dir` = `context/scan/<date>/`(`_brief_sources.json` + `_relative_buy_decision.json`),
    缺省 = `report_dir`(便于对同一目录的合成夹具跑)。返回 `[{check,severity,detail,code}]`。

    ⑤ **BUY≥1 契约只在 active 模式生效**:`mode` 从 `_relative_buy_decision.json` 读;影子期
    该检查跳过,但**出一条 info 留痕**——静默跳过会让「这道门什么时候开始管事」不可查
    (recalibrate 空转 2 周的同族教训)。

    ⑥ **③ 段相对 BUY 与决策文件同源**(E4):brief ③ 段渲染出的六位代码集合必须等于
    `buys[].code` 集合(含空对空)——08-17 事故:brief 读到了决策文件早 25 秒的半成品,
    把本该 rank1 的 BUY 印成了 BLOCKED,两层报告一起错、lint 一起绿。decision 缺失时
    跳过(缺席由 ⑤ 的 mode=ABSENT 留痕负责)。**不做 mtime 顺序检查**——决策文件有两个
    合法写者(`publisher._run_publish` 在 brief 之前;`post_run observe` 在 brief 之后
    无条件重写),mtime 顺序在健康日也会颠倒,该判据结构性不可用(2026-08-19 复核 Critical)。

    **severity 不在调用点各写各的**:一律由 `BRIEF_LINT_SEVERITY` 查表(B-2 裁定的单一
    事实源;只有影子期那条 info 留痕显式传 `severity`)。GATE4 拦不拦这条,读那张表即知。
    """
    import contextlib
    import json
    import re
    from pathlib import Path

    out: list[dict] = []

    def add(check, detail, code=None, *, severity=None):
        out.append({"check": check,
                    "severity": severity or BRIEF_LINT_SEVERITY.get(check, "fail"),
                    "detail": detail, "code": code})

    report = Path(report_dir)
    scan = Path(scan_dir) if scan_dir is not None else report
    try:
        from autoresearch.scan import brief as _brief
    except Exception as exc:  # noqa: BLE001 — 模块都装不上就只报一条,不炸
        add("brief·lint不可用", f"brief 模块不可导入:{type(exc).__name__}", severity="warn")
        return out

    # ① 在场 + ② 预算
    path = report / _brief.BRIEF_FILENAME
    if not path.exists():
        add("brief·缺失",
            f"{path} 不存在 —— 报告双层的速读层没落盘(publisher 接线断了?)")
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        add("brief·缺失", f"{path} 读不出:{type(exc).__name__}")
        return out
    n_bytes = len(text.encode("utf-8"))
    if n_bytes > _brief.MAX_BYTES:
        add("brief·超预算",
            f"{n_bytes}B > 硬预算 {_brief.MAX_BYTES}B —— 速读层撑破了就不再是速读层")

    # ③ sources 边表:重算 + 锚在 + 白名单
    payload = None
    with contextlib.suppress(Exception):
        payload = json.loads((scan / _brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        add("brief·边表缺失",
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
                add("brief·边表过期",
                    f"边表与白名单输入重算结果不符({len(rows)} vs "
                    f"{len(fresh['sources'])} 行;首个差异 {stale[:3] or '行数'})"
                    " —— 输入变了但产物没重生成")
        bad_anchor = [r.get("field") for r in rows if str(r.get("text") or "") not in text]
        if bad_anchor:
            add("brief·数字对账",
                f"{len(bad_anchor)} 个字段的渲染片段不在 brief 正文里:"
                f"{'、'.join(str(f) for f in bad_anchor[:5])}"
                " —— brief 被手改过,或渲染与边表脱钩")
        outside = sorted({str(r.get("file")) for r in rows
                          if r.get("file") not in _brief.INPUT_WHITELIST})
        if outside:
            add("brief·白名单外取数",
                f"{outside} 不在 `brief.INPUT_WHITELIST` —— 生成器禁读 details 全文与 trace 大文件")

    # ④ brief ↔ summary(四个决策字段的**同一片段**必须两边都在)
    summary_path = report / "summary.md"
    summary = ""
    with contextlib.suppress(Exception):
        summary = summary_path.read_text(encoding="utf-8")
    if not summary:
        add("brief↔summary不一致",
            f"{summary_path} 缺失/空 —— 无法验证两层报告说的是同一件事")
    else:
        by_field = {str(r.get("field")): r for r in rows}
        missing = [f for f in _BRIEF_DECISION_FIELDS
                   if f in by_field and str(by_field[f].get("text") or "") not in summary]
        if missing:
            add("brief↔summary不一致",
                f"决策字段在 summary 的 🧭 仪表盘块里对不上:{'、'.join(missing)}"
                " —— 两层报告的 BUY 数/code/basis/基准读数必须同源同值")

    # ⑤ active 期 BUY 契约(影子期跳过并留痕)
    decision = None
    with contextlib.suppress(Exception):
        decision = json.loads((scan / _brief.DECISION_FILENAME).read_text(encoding="utf-8"))
    mode = str((decision or {}).get("mode") or "ABSENT")
    if mode != "active":
        # 唯一显式传 severity 的调用点:这不是**判据触发**,是「本判据今天没生效」的留痕。
        add("brief·BUY契约(active 期)",
            f"mode={mode} —— 非 active,「成功 run 必须至少 1 只 BUY」与「BLOCKED 不得渲染成"
            "成功」两条跳过(影子期只观察,不拦发布);活体切换后本条自动生效",
            severity="info")
    else:
        blocked = bool((decision or {}).get("blocked"))
        n_buys = len((decision or {}).get("buys") or [])
        if blocked:
            if "BLOCKED" not in text:
                add("brief·BUY契约(active 期)",
                    "决策文档 blocked=true 但 brief 没渲染 BLOCKED —— 故障被写成了成功 run")
        elif n_buys < 1:
            add("brief·BUY契约(active 期)",
                f"成功 run 的 BUY_n={n_buys}<1 —— active 期每个成功交易日至少一只(E6 裁定)")

    # ⑥ ③ 段相对 BUY 与决策文件同源(E4,`docs/specs/2026-08-18-e6-activation-learning-
    # slimdown-design.md` §3 E4)—— 08-17 事故:brief 读到了 `_relative_buy_decision.json`
    # 早 25 秒的半成品,把本该 rank1 的 BUY 印成了「BLOCKED·合格 0」,两层报告一起错、
    # lint 一起绿。brief ③ 段渲染出的六位代码集合必须等于决策文件 `buys[].code` 集合
    # (含空对空:decision 无 buys/blocked 时,brief 也不得印出任何六位代码)—— 违反即
    # 「报告在说假话」= fail。decision 缺失时整条跳过 —— 缺席已由 ⑤ 的 mode=ABSENT
    # 留痕负责,这里只管「两边都在但对不上」。check 名刻意不带 `产物形状·`/
    # `usage_reconcile·` 前缀 —— 那两个在 `common.failclass.EXEMPT_PREFIXES` 里被判
    # hygiene/metering,会被豁免出 data 类;说假话就是数据不可信,必须落 data 类、连坐
    # 当日决策(`fail_class` 未登记前缀一律 data,fail-safe 默认)。
    #
    # 不做 mtime 顺序检查:`_relative_buy_decision.json` 有两个合法写者 ——
    # `publisher._run_publish`(brief 之前)与 `post_run observe`(STAGES 步骤 5,brief 之后
    # 无条件重写)。故「决策比 brief 新」在健康日也成立(2026-08-17 实证:22:15:36 vs
    # 22:15:33、内容同源)。真正要防的「brief 读了半成品」由上面的**内容同源**判据直接抓
    # (复核 Critical,2026-08-19:原设计稿曾同时收一条 mtime 顺序探针,侦察漏查了
    # `post_run observe` 这第二个写者 —— 已删,勿再补回)。
    if isinstance(decision, dict):
        decision_codes = {str(row.get("code")) for row in (decision.get("buys") or [])
                          if isinstance(row, dict) and row.get("code")}
        buy_line = next((ln for ln in text.splitlines()
                         if "relative BUY" in ln and ("🕶" in ln or "✅" in ln)), None)
        brief_codes = set(re.findall(r"\b\d{6}\b", buy_line)) if buy_line else set()
        if brief_codes != decision_codes:
            add("brief③相对BUY与决策文件不同源",
                f"brief ③ 段渲染代码 {sorted(brief_codes) or ['无']} != 决策文件 "
                f"buys[].code {sorted(decision_codes) or ['无']}"
                " —— 相对 BUY 必须两边同源(含空对空);08-17 事故同形")
    return out


def brief_lint_banner(rows: list[dict]) -> str:
    """brief lint 的 CP7 播报行(B-2)。

    **降级不等于消音**:B-2 把四条判据从 fail 降到 warn 之后,只数 fail 的播报行会让
    「brief 没落盘 / 撑破 3KB」变成一句「fail 0」——人再也看不见。所以 fail 与 warn
    **两个计数都播**,并逐条列出(🛑 fail / ⚠️ warn),info 只计数不刷屏。
    """
    n_fail = sum(1 for x in rows if x.get("severity") == "fail")
    n_warn = sum(1 for x in rows if x.get("severity") == "warn")
    head = f"[brief lint] fail {n_fail} · warn {n_warn} / 共 {len(rows)} 条"
    detail = "".join(
        f"\n  {'🛑' if x.get('severity') == 'fail' else '⚠️'} {x.get('check')}:{x.get('detail')}"
        for x in rows if x.get("severity") in ("fail", "warn"))
    return head + detail


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

    无 failures 也写表头(区分"没拦"与"没跑")。
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


#: banner 聚合器的数字 token(§6.6 范围摘要)。
_BANNER_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
#: 摘要在此分隔符处收尾(后面是每条都一样的解释性长尾,聚合时没有信息量)。
_BANNER_TAIL_SEPS = ("——", " — ", ";", ";")
#: 聚合摘要的展示上限(字符);超出截断加 `…`。
_BANNER_SUMMARY_MAX = 60
#: 摘要两端的悬挂标点(在分隔符处收尾后会留下)。
_BANNER_EDGE_RE = re.compile(r"^[\s·—;;,,、]+|[\s·—;;,,、]+$")


def _banner_groups(failures: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """`failures` → `[(mark, check, rows)]`:按 lint key 分组,**fail 组永远排在 warn 组前**。

    `list.sort` 稳定 ⇒ 同severity 内保持首现序,组内保持原顺序(聚合的是版面,不是事实)。
    """
    order: list[tuple[str, str]] = []
    buckets: dict[tuple[str, str], list[dict]] = {}
    for x in failures:
        key = ("fail" if x.get("severity") == "fail" else "warn", str(x.get("check", "")))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(x)
    order.sort(key=lambda k: 0 if k[0] == "fail" else 1)
    return [("🛑" if k[0] == "fail" else "⚠️", k[1], buckets[k]) for k in order]


def _banner_range_summary(details: list[str]) -> str:
    """同 key 多行 → 一句范围摘要(如 `自报 22–37 条 > cap 20`);抽不出可比数字 → `""`。

    判据是**骨架逐字相同**(把数字挖空后相等):只有同一句话的同一个数位才能求 min–max。
    把不同句子里的数字混进同一个范围,正是本仓「量错对象」家族的病(08-28 R1 时间锚同款)
    —— 宁可只印 `×n`,也不给一个看起来精确的假区间。
    """
    if not details:
        return ""
    skels = [_BANNER_NUM_RE.sub("\x00", d) for d in details]
    nums = [_BANNER_NUM_RE.findall(d) for d in details]
    if len(set(skels)) != 1 or len({len(n) for n in nums}) != 1:
        return ""                              # 骨架不同 → 数字不可比,只印 ×n
    parts, slots, varying = skels[0].split("\x00"), [], set()
    for i in range(len(nums[0])):
        vals = [n[i] for n in nums]
        if len(set(vals)) == 1:
            slots.append(vals[0])
        else:
            ordered = sorted(vals, key=float)
            slots.append(f"{ordered[0]}–{ordered[-1]}")
            varying.add(i)
    text, keep_to = "", 0
    for i, seg in enumerate(parts):
        text += seg
        if i < len(slots):
            text += slots[i]
            if i in varying:
                keep_to = len(text)            # 收尾不得砍掉任何一个变化了的数位
    cut = len(text)
    for sep in _BANNER_TAIL_SEPS:
        j = text.find(sep, keep_to)
        if j >= 0:
            cut = min(cut, j)
    text = _BANNER_EDGE_RE.sub("", text[:cut])
    if len(text) > _BANNER_SUMMARY_MAX:
        text = text[:_BANNER_SUMMARY_MAX - 1] + "…"
    return text


def render_banner(result: dict, aggregate: bool = False) -> str:
    """自检结果 → 报告顶部 banner(有 fail 醒目拦截,有 warn 提示)。无问题返回空串。

    `aggregate=False`(默认)= 逐条一行,**与历史输出逐字节一致** —— appendix A 用它出全文。

    `aggregate=True`(§6.6,summary 决策层用)= 按 lint key 分组:`**key**:×n(范围摘要)`;
    `n == 1` 原样输出该行;fail 组永远在 warn 组前。08-26 实测 13 行只有 5 种 key,
    `产物形状·intel限频` 同一句话重复 8 遍占 summary 8.5% 字节 —— 聚合掉的是**版面**,
    顶行 `fail N / warn M` 仍是**逐条**计数,全文仍在 appendix A(不删事实,只去重复)。
    """
    if not result["failures"]:
        return ""
    icon = "🛑 自检未通过(发布前须先修根因)" if result["n_fail"] else "⚠️ 自检提示"
    lines = [f"> {icon} — fail {result['n_fail']} / warn {result['n_warn']}"]
    if not aggregate:
        for x in result["failures"]:
            mark = "🛑" if x["severity"] == "fail" else "⚠️"
            lines.append(f"> {mark} **{x['check']}**:{x['detail']}")
        return "\n".join(lines) + "\n"
    for mark, check, rows in _banner_groups(result["failures"]):
        if len(rows) == 1:
            lines.append(f"> {mark} **{check}**:{rows[0].get('detail', '')}")
            continue
        summary = _banner_range_summary([str(r.get("detail", "")) for r in rows])
        lines.append(f"> {mark} **{check}**:×{len(rows)}"
                     + (f"({summary})" if summary else ""))
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
    print("SELFTEST ✅  覆盖率/经验红线(获利盘满·override)/评级矛盾/行业集中/空泛话术"
          "/评级超rubric(C)/banner 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
