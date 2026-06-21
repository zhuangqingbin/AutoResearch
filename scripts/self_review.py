#!/usr/bin/env python3
"""发布前机械自检硬门(UZI「self-review gate」本地版)· 纯函数可自测,零 LLM。

把"违背已学经验 / 评级-因子矛盾 / 覆盖不足 / 行业过度集中 / 空泛话术"做成发布前的机械检查:
**有 fail 就不该直接发,先修根因**(assemble 把 fail 顶到报告最前作 banner;skill 据此先改)。
与闭环耦合:经验红线直接来自 factor_lab 的 T+1 IC 校准(winner_rate 满=抛压、过热=回避)+
`feedback_store` 的结构化 guard(lesson 带 {field,op,value} 时自动纳入)。

用法:uv run --no-sync python scripts/self_review.py --selftest
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


def review(ctx: dict) -> dict:
    """机械自检。ctx: {finalists:[{code,rating,composite,winner_rate,pct_60d,rsi6,sector,override?}],
    n_cards_expected, n_cards_present, summary_text, lessons:[{id,guard?}], 阈值可选}。

    返回 {ok, n_fail, n_warn, failures:[{check,severity,detail}]}。severity ∈ {fail,warn}。
    """
    failures: list[dict] = []

    def add(check, sev, detail):
        failures.append({"check": check, "severity": sev, "detail": detail})

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
                f"{code} 买入但 winner_rate {wr:.0f}>88(IC:抛压/见顶),需特批 override")
        if p60 is not None and rsi is not None and p60 > 50 and rsi > 80:
            add("经验红线·过热", "warn", f"{code} 买入但过热(60日 {p60:.0f}% + RSI6 {rsi:.0f}>80)")
        if comp is not None and comp < comp_floor:
            add("评级-因子矛盾", "warn", f"{code} 买入但 composite {comp:.0f} < {comp_floor:.0f}")

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
                    f"{f.get('code', '?')} 触发经验红线 {gd.get('field')}{gd.get('op')}{gd.get('value')}")

    # 6) 评级超 rubric 评分卡建议(C·LLM-as-judge:防 gestalt 过度多报;有 偏离/override 说明则豁免)
    for f in buys:
        if f.get("override") or f.get("rubric_dev"):
            continue
        rs, rt = f.get("rubric_suggest"), f.get("rating")
        if rs in _RANK and rt in _RANK and _RANK[rt] < _RANK[rs]:
            add("评级超rubric", "warn",
                f"{f.get('code', '?')} 评级 {rt} 激进于评分卡建议 {rs}(需 **偏离** 说明或下修)")

    n_fail = sum(1 for x in failures if x["severity"] == "fail")
    return {"ok": n_fail == 0, "n_fail": n_fail, "n_warn": len(failures) - n_fail,
            "failures": failures}


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
