#!/usr/bin/env python3
"""发布前机械自检硬门(UZI「self-review gate」本地版)· 纯函数可自测,零 LLM。

把"违背已学经验 / 评级-因子矛盾 / 覆盖不足 / 行业过度集中 / 空泛话术"做成发布前的机械检查:
**有 fail 就不该直接发,先修根因**(assemble 把 fail 顶到报告最前作 banner;skill 据此先改)。
与闭环耦合:经验红线直接来自 factor_lab 的 T+1 IC 校准(winner_rate 满=抛压、过热=回避)+
`feedback_store` 的结构化 guard(lesson 带 {field,op,value} 时自动纳入)。

用法:uv run --no-sync python -m autoresearch.learning.self_review --selftest
"""
from __future__ import annotations

import sys
from collections import Counter

_BUY = ("Overweight", "Buy")
_BANNED = ("基本面良好", "前景广阔", "值得关注", "建议关注")
_TIER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
_RANK = {r: i for i, r in enumerate(_TIER)}  # 越小越多头


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


def intel_future_dates_lint(scan_dir, date_str: str) -> list[dict]:
    """intel as-of 前视机检(advisory;design: 2026-07-12-l4-intel-station-plan.md Task 6)。

    逐 `_l4_intel_*.md`(l4-intel 盲搜落稿)**只查「## 事件段」表格行首日期列**
    (`| YYYY-MM-DD | ... |`)——事件**正文**里提到的未来催化时点(如「8-15 披露中报」)合法,
    不算前视穿越,不查。命中晚于扫描日的表格行日期 → `severity="warn"`(advisory,不挡发布)。

    scan_dir:通常 = `context/scan/<date>`;date_str 按 `dump_gate_fires` 同款惯例由调用方传
    `scan_dir.name`(数据日,`YYYY-MM-DD`)。缺 `_l4_intel_*.md`(未启用/未派发)→ 空列表。
    """
    import re
    from pathlib import Path
    scan_dir = Path(scan_dir)
    out: list[dict] = []

    def add(check, sev, detail, code=None):
        out.append({"check": check, "severity": sev, "detail": detail, "code": code})

    for p in sorted(scan_dir.glob("_l4_intel_*.md")):
        in_events, future = False, []
        for line in p.read_text(encoding="utf-8").splitlines():
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


def product_shape_lint(scan_dir, date_str: str) -> list[dict]:
    """产物形状 lint(九探针,零 LLM;design: 2026-07-13-next-optimization-survey.md 线 C
    + 2026-07-22 dossier design Wave1 ⑤ + 2026-07-23 终审 I-2)。

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
       保送票同走 l4-stock 链、同派 intel)− ♻️ 复用数(复用痕迹 = `details/<code>.md` 含
       ♻️ banner,l4_reuse.write_reused_card 所落;details/ 缺 → 期望=全行数,detail 注明
       口径)。0 份 intel = 未启用,本条不出。07-17 实测:10 行 − 1 复用 = 9 稿 ✓。
    4. **anns 去伪**(info):`anns_empty_rate`==1.0 = expected/no-permission(公告面已由
       news_em+intel 覆盖),明置非告警(线 D 退役配套)。
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

    全部 presence-gated:缺文件/缺键/坏文件 → 该条静默跳过,**绝不抛异常**。
    返回 [{check,severity,detail,code}](severity ∈ {warn,info});接线在 assemble
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

    reused: set[str] = set()          # ♻️ 复用卡(l4_reuse banner;派发时被跳过的票)
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
            from autoresearch.scan.agents.l4_card import force_full_card
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

    # 3) intel 稿数 = 全 finalist 行(含保送,皆走 l4-stock 链派 intel)− 复用(0 份 = 未启用,不出本条)
    if intel_files and fin_loaded:
        n_rows = len(fin_rows)
        if (scan_dir / "details").is_dir():
            n_reuse = len({r["code"] for r in fin_rows} & reused)
            expect, cal = n_rows - n_reuse, f"finalist 行 {n_rows}(含保送) − ♻️ 复用 {n_reuse}"
        else:
            expect, cal = n_rows, f"finalist 行 {n_rows}(含保送;details/ 缺,复用数不可得)"
        if len(intel_codes) != expect:
            add("产物形状·intel稿数不符", "warn",
                f"intel 稿 {len(intel_codes)} 份 ≠ 期望 {expect}({cal})")

    # 4) anns 去伪告警(线 D:expected 无权限 ≠ 当日故障)
    rate = health.get("anns_empty_rate")
    if rate is not None:
        with contextlib.suppress(TypeError, ValueError):
            if float(rate) == 1.0:
                add("产物形状·anns去伪", "info",
                    "anns_empty_rate=1.0 = expected/no-permission(公告面已由 news_em+intel 覆盖)"
                    ",非当日故障告警")

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
    for code, text in sorted(cards.items()):
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            res = price_claims.audit_card_text(
                text, name=name_by_code.get(code, ""), code6=code, date=date_str,
                bars_fn=price_claims.bars_for)
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
    return out


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

    from autoresearch.scan.assemble import gate_status

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
