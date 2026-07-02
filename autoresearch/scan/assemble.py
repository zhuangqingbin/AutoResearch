#!/usr/bin/env python3
"""scan-market v2 · L5 整合阶段 —— 漏斗溯源 + 三段 summary + trace/ 发布。

design: docs/specs/2026-06-20-scan-market-v2-design.md(§7 整合)

读 context/scan/<date>/ 的漏斗产物(meta.json 计数 + L1_recall_top1000.csv 召回 +
L2_coarse_keep200.csv 粗排 + finalists.csv 精排[带 thesis/risk/catalyst] + details/<ticker>.md
L4 决策卡),用项目 parse_rating 提五档评级 + 仪表盘,产出三段 summary:
  1. 漏斗数量      —— 选集→召回→粗排→精排→研究 各阶段出量 + 卡点标准
  2. 各阶段卡点 & 股票概览 —— 逐阶段"砍了什么/活下来哪类票/代表股"
  3. 投资建议      —— buy-list(评级/目标/R:R)+ 组合视角 + 诚实局限
发布到 reports/scan/<运行日YYYYMMDD>_<HHMM>/(summary.md + details/〈名称〉.md + trace/ 溯源 + manifest.json
〔记数据日 analysis_date,供 retro 按数据日定位本报告——目录名是运行时刻,与数据日解耦〕)。

纯确定性(stdlib + parse_rating),零 LLM。selftest 已迁 pytest(tests/scan/test_assemble.py)。

用法:
  uv run --no-sync python -m autoresearch.scan.assemble 2026-06-20
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating

TIER_RANK = {r: i for i, r in enumerate(RATINGS_5_TIER)}  # Buy=0 … Sell=4

_PROPOSAL_RE = re.compile(r"FINAL TRANSACTION PROPOSAL[:\s*]*\**\s*(BUY|HOLD|SELL)", re.IGNORECASE)
_CONF_RE = re.compile(r"置信度[:：]\s*\**\s*([高中低]+)")
# C·评分卡建议(卡片 `**Rubric建议**: <Rating>...`)+ 偏离说明(`**偏离**:...`)→ self_review 比对
_RUBRIC_RE = re.compile(r"Rubric[^\n]*?(Buy|Overweight|Hold|Underweight|Sell)", re.IGNORECASE)
_DEV_RE = re.compile(r"\*\*\s*偏离\s*\*\*")
# L4 一句话结论(给 buy-list 的『L4研究』列):一行多空 / 早停因 / 满卡多空对撞首条
_BULLBEAR_RE = re.compile(r"\*\*一行多空\*\*[:：]?\s*(.+)")
_STOPWHY_RE = re.compile(r"早停因[:：]\s*(.+?)(?:→|\*\*|$)")
_BEARBULLET_RE = re.compile(r"[-•]\s*空[:：]\s*(.+)")


# ───────────────────────── 解析 helpers ─────────────────────────


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _strip(s: str | None) -> str:
    # '|' → '/':去掉会劈裂 markdown 表格列的管道符(L3 自由文本里偶发);'**' 去强调标记。
    return (s or "").replace("**", "").replace("|", "/").strip()


_VERDICT_BADGE = {"维持": "✅维持", "降级": "⚠️降级", "否决": "🛑否决"}


def _load_verify(scan_dir: Path) -> dict[str, dict]:
    """读 Tier-3 多空辩论 verify.csv(code,verdict,bull,bear,trigger,consensus)→ {code: {...}}。

    bull(最强多头)+ consensus(PM 3 透镜共识)是 A/B 新增列;老 4 列 schema(无 bull/consensus)
    仍兼容,缺列回空串(无 verify.csv 则整表空,老路不破)。
    """
    out: dict[str, dict] = {}
    for r in _read_csv(scan_dir / "verify.csv"):
        if r.get("code"):
            out[str(r["code"]).strip().zfill(6)] = {
                "verdict": _strip(r.get("verdict", "")), "bull": _strip(r.get("bull", "")),
                "bear": _strip(r.get("bear", "")), "trigger": _strip(r.get("trigger", "")),
                "consensus": _strip(r.get("consensus", ""))}
    return out


def _verify_badge(code: str, vmap: dict[str, dict]) -> str:
    v = vmap.get(str(code).zfill(6))
    return _VERDICT_BADGE.get(v["verdict"], v["verdict"]) if v else "—"


def _apply_verify_downgrade(rating: str, verdict: str) -> str:
    """Tier-3 红队折回评级:降级=降一档、否决=至少 Hold(踢出 ≥OW 买单);维持/未验=不变。

    解决『OW⚠️降级』自相矛盾——买单上不该挂系统自己都不信的评级。
    """
    idx = TIER_RANK.get(rating, 99)
    if idx >= len(RATINGS_5_TIER):
        return rating
    if verdict == "降级":
        idx = min(idx + 1, len(RATINGS_5_TIER) - 1)
    elif verdict == "否决":
        idx = max(idx, TIER_RANK["Hold"])
    return RATINGS_5_TIER[idx]


_PROPOSAL_BY_RATING = {"Buy": "BUY", "Overweight": "BUY", "Hold": "HOLD",
                       "Underweight": "SELL", "Sell": "SELL"}


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


def _parse_dashboard(text: str) -> dict[str, str]:
    """取决策卡里第一张含『评级』的表(决策仪表盘),按表头→数据配成 dict。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("|") and "评级" in s and i + 2 < len(lines):
            header = [c.strip() for c in s.strip("|").split("|")]
            data = [_strip(c) for c in lines[i + 2].strip().strip("|").split("|")]
            if len(data) == len(header):
                return dict(zip(header, data, strict=True))
    return {}


def _get(d: dict[str, str], *needles: str) -> str:
    for k, v in d.items():
        if any(n in k for n in needles):
            return v
    return ""


def _clip(s: str, n: int = 48) -> str:
    """收紧成表格单元格:去 markdown/管道符、顿号→中点、截断加省略号。"""
    s = _strip(s).replace("、", "·").strip("=。;； ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _l4_brief(text: str, rating: str) -> str:
    """L4 决策卡的一句话结论(buy-list『L4研究』列):≥OW 取『一行多空』多头驱动,
    否则取空头/早停因(为何没给买点)。回退满卡多空对撞首条空。"""
    m = _BULLBEAR_RE.search(text)
    if m:
        segs = re.split(r"[｜|]", m.group(1))
        want = "多" if rating in ("Buy", "Overweight") else "空"
        seg = next((s for s in segs if s.strip().startswith(want)), "")
        if seg:
            return _clip(seg.strip().lstrip("多空").strip(" :："))
    m = _STOPWHY_RE.search(text)
    if m:
        return _clip(m.group(1))
    m = _BEARBULLET_RE.search(text)
    if m:
        return _clip(m.group(1))
    return "—"


def _decision_text(scan_dir: Path, ticker: str) -> str | None:
    """定位 finalist 的 lite 决策卡:context/scan/<date>/details/<ticker>.md,按 6 位代码 glob 兜底。"""
    base = scan_dir / "details"
    code = ticker.split(".")[0]
    tries = [base / f"{ticker}.md"]
    if base.is_dir():
        tries += sorted(p for p in base.glob(f"{code}*.md"))
    seen: set[Path] = set()
    for p in tries:
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def _finalist_row(scan_dir: Path, fr: dict) -> dict:
    ticker = (fr.get("ticker") or fr.get("code") or "").strip()
    text = _decision_text(scan_dir, ticker)
    if text is None:
        return {**fr, "rating": "—", "target": "⚠️卡片缺失", "rr": "—", "proposal": "—",
                "conf": "—", "l4": "⚠️卡片缺失"}
    dash = _parse_dashboard(text)
    conf = _get(dash, "置信度")
    if not conf:
        m = _CONF_RE.search(text)
        conf = m.group(1) if m else "—"
    prop = _PROPOSAL_RE.search(text)
    rub = _RUBRIC_RE.search(text)
    rating = parse_rating(text)
    return {
        **fr,
        "rating": rating,
        "target": _get(dash, "EV目标", "目标") or "—",
        "rr": _get(dash, "R:R") or "—",
        "proposal": prop.group(1).upper() if prop else "—",
        "conf": conf or "—",
        "l4": _l4_brief(text, rating),                            # L4 深核一句话结论(买列『L4研究』)
        "rubric_suggest": rub.group(1).title() if rub else "",   # C·评分卡建议(self_review 比对)
        "rubric_dev": bool(_DEV_RE.search(text)),                # 卡片有 **偏离** 说明 → 豁免
    }


def _sortkey(r: dict):
    tier = TIER_RANK.get(r.get("rating", ""), 99)
    try:
        conv = float(r.get("conviction") or 0)
    except ValueError:
        conv = 0.0
    return (tier, -conv)


# ───────────────────────── 三段 summary ─────────────────────────


def _funnel_rows(meta: dict, n_l2, n_l3, n_cards) -> list[str]:
    l2_eng = meta.get("l2_engine", "GBDT")
    return [
        "| 阶段 | 名称 | 出量 | 引擎 | 卡点标准 |", "|---|---|---:|---|---|",
        f"| L0 | 选集 | {meta.get('universe', '?')} | 确定性 | 全A {meta.get('universe_raw', '?')} → 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
        f"| L1 | 召回 | {meta.get('recall_n', '?')} | 确定性 | 轻门 + 行业条件化复合分(T+1 IC 校准) top |",
        f"| L2 | 粗排 | {n_l2} | GBDT/{l2_eng} | LightGBM 学习重排(T+1 IC 训练;oos 未胜线性则回落复合分) |",
        f"| L3 | 精排 | {n_l3} | Opus-high·holistic | 1 agent 通看 ~200 比较选 + 增量证据/论点/红队 |",
        f"| L4 | 研究 | {n_cards} 卡 | Opus | 一只=一个 Opus subagent 渐进深度 DD + 早停 + 买单 skeptic |",
    ]


_CH_ZH = {"composite": "复合", "momentum": "动量", "reversal": "反转", "growth": "成长",
          "value": "价值", "main_fund": "主力", "northbound": "北向",
          "accumulation": "吸筹", "heat": "热度"}


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
    """L2 粗排结论:#GBDT重排名次(分母在列头)· gbdt 分。"""
    r = l2_top.get(str(code).zfill(6))
    if not r:
        return "—"
    g = r.get("gbdt_score")
    try:
        gtxt = f"·g{float(g):.2f}" if g not in (None, "", "nan") else ""
    except (TypeError, ValueError):
        gtxt = ""
    return f"#{r.get('l2_rank', '?')}{gtxt}"


_BYTES_PER_TOK = 2.8   # CJK 混合文本粗估(中文≈3字节/字≈1+ token,夹杂 ASCII 数字/markdown)


def _stage_token_estimate(scan_dir: Path) -> list[str]:
    """分阶段 token **粗估**(确定性,无 LLM):按落盘的推理稿/决策卡**输出字节** ÷ 2.8 估 ~token + 调用计数。

    口径诚实:**输入侧**(喂 subagent 的 slim 上下文/紧凑表)多未留痕 → 真实总量数倍于此,本表为可测下界;
    L0/L1/L2 确定性层 = 0 LLM。要精确计量需在编排层逐次记 usage。
    """
    det = scan_dir

    def _b(files) -> int:
        return sum(p.stat().st_size for p in files if p.is_file())

    cards = sorted((det / "details").glob("*.md")) if (det / "details").is_dir() else []
    l3 = list(det.glob("_l3*"))
    l4t1 = list(det.glob("_l4_batch*")) + list(det.glob("_l4_prompt*"))
    verify = list(det.glob("_v_*"))
    rows = [
        ("L0 选集", "确定性", 0, 0, "纯 pandas 硬门"),
        ("L1 召回", "确定性", 0, 0, "复合分排序"),
        ("L2 粗排", "确定性·GBDT", 0, 0, "LightGBM 重排,零 LLM"),
        ("L3 精排", "Opus-high·holistic", 1 if l3 else 0, _b(l3), "1 agent 通看 ~200 选 30"),
        ("L4 研究", "Opus", len(cards), _b(cards) + _b(l4t1), f"{len(cards)} 张卡(早停卡/满卡)"),
        ("L4 买单 skeptic", "Opus", len(verify), _b(verify), "≥OW 买单独立证伪"),
    ]
    lines = ["## 各阶段 token 消耗(估算)",
             "| 阶段 | 引擎 | LLM 调用 | 输出字节 | ~输出token | 说明 |",
             "|---|---|---:|---:|---:|---|"]
    tot_calls = tot_tok = 0
    for name, eng, calls, b, note in rows:
        tok = int(b / _BYTES_PER_TOK)
        tot_calls += calls
        tot_tok += tok
        lines.append(f"| {name} | {eng} | {calls or '—'} | {b or '—'} | {tok or '—'} | {note} |")
    lines.append(f"| **合计** | — | **{tot_calls}** | — | **~{tot_tok}** | 输出侧下界 |")
    lines += ["", "> 口径:**输出落盘字节 ÷ 2.8**(CJK 混合粗估)。**输入侧未全留痕**(L4 每卡 slim 上下文数千 "
              "token × 卡数才是大头)→ **真实总 token 数倍于此表**,此为可测下界。L0/L1/L2 确定性 = 0 LLM。", ""]
    return lines


def _stage_overview(label: str, rows: list[dict], reason: str) -> list[str]:
    if not rows:
        return [f"\n**{label}** — _无 staging,跳过_"]
    inds = Counter(r.get("industry", "") for r in rows if r.get("industry"))
    top = "、".join(f"{k}({v})" for k, v in inds.most_common(5)) or "—"
    reps = ", ".join(str(r.get("name", "")) for r in rows[:6])
    return [f"\n**{label}** — {reason}", f"- 行业分布 top5:{top}", f"- 代表股:{reps}"]


def _portfolio_note(rows: list[dict]) -> str:
    secs = Counter((r.get("sector") or r.get("industry") or "?") for r in rows)
    top = "、".join(f"{k}×{v}" for k, v in secs.most_common(5))
    buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    return (f"买入/超配 **{buys}** 只;板块集中度:{top or '—'}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,催化日历做节奏。")


def _knowledge_note(rows: list[dict]) -> str:
    """浮出与 buy-list 标的/行业相关的 active 经验 + 未决反馈(闭环记忆注回报告骨架)。

    store 空 / feedback_store 不可用 → 返回空串(向后兼容,老路径不破)。
    """
    try:
        import autoresearch.learning.feedback_store as fs
    except Exception:  # noqa: BLE001 — 知识库是可选层,缺了不影响出报告
        return ""
    codes = {str(r.get("code")) for r in rows if r.get("code")}
    scopes: list = [("global", "*")]
    for r in rows:
        if r.get("code"):
            scopes.append(("ticker", str(r["code"])))
        ind = r.get("sector") or r.get("industry")
        if ind:
            scopes.append(("industry", ind))
    try:
        lessons = fs.lessons_for(scopes)
        open_fb = [f for f in fs._read_jsonl(fs._FEEDBACK)
                   if f.get("status") == "open"
                   and (f.get("scope", {}).get("kind") == "global"
                        or f.get("scope", {}).get("value") in codes)]
    except Exception:  # noqa: BLE001
        return ""
    if not lessons and not open_fb:
        return ""
    lines = ["## 📌 经验 / 未决反馈(闭环记忆)"]
    if lessons:
        lines.append("**生效经验**(已注入 L2/L3 校准 + 本次研判):")
        for lsn in lessons[:8]:
            sc = lsn.get("scope", {})
            tag = "" if sc.get("kind") == "global" else f"[{sc.get('value')}] "
            lines.append(f"- {tag}{lsn['rule']}  _(conf {lsn.get('confidence', 0):.2f})_")
    if open_fb:
        lines.append("**未决反馈**(待 retro / 后续消化):")
        for f in open_fb[:6]:
            lines.append(f"- ({f.get('verdict')}) {str(f.get('note', ''))[:50]} — `{f.get('id')}`")
    return "\n".join(lines) + "\n"


def _load_market_view(scan_dir: Path) -> str:
    """读 L2 后策略师写的 market_view.md staging(缺 → '')。assemble 仍零-LLM(只读文件)。"""
    p = scan_dir / "market_view.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


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


def _self_review_banner(scan_dir: Path, rows: list[dict], summary_text: str,
                        regime_drift: str = "") -> str:
    """发布前机械自检(self_review 硬门)→ 报告顶部 banner。缺依赖/无问题 → 空串(老路不破)。"""
    try:
        import autoresearch.learning.self_review as self_review
    except Exception:  # noqa: BLE001
        return ""
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
                       "rubric_suggest": r.get("rubric_suggest"), "rubric_dev": r.get("rubric_dev")})
    n_present = sum(1 for r in rows if r.get("target") != "⚠️卡片缺失")
    lessons = []
    try:
        import autoresearch.learning.feedback_store as fs
        lessons = fs.lessons_for([("global", "*")])
    except Exception:  # noqa: BLE001
        pass
    ctx = {"finalists": finals, "n_cards_expected": len(rows), "n_cards_present": n_present,
           "summary_text": summary_text, "lessons": lessons, "regime_drift": regime_drift}
    import contextlib
    res = self_review.review(ctx)
    with contextlib.suppress(Exception):
        self_review.dump_gate_fires(scan_dir, res, scan_dir.name)   # R3 留痕;IO 失败不阻发布
    return self_review.render_banner(res)


def build_summary(scan_dir: Path, analysis_date: str, hhmm: str, folder: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    recall = _read_csv(scan_dir / "L1_recall_top1000.csv")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    l1_full = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    l2_top = {str(r.get("code", "")).zfill(6): r for r in keep}
    rows = [_finalist_row(scan_dir, fr) for fr in finals]
    vmap = _load_verify(scan_dir)   # Tier-3 对抗验证;降级/否决折回评级(踢出买单),无 verify.csv 则空(老路不破)
    for r in rows:
        v = vmap.get(str(r.get("code", "")).zfill(6))
        if v and v["verdict"] in ("降级", "否决"):
            r["rating"] = _apply_verify_downgrade(r.get("rating", "Hold"), v["verdict"])
            r["proposal"] = _PROPOSAL_BY_RATING.get(r["rating"], r.get("proposal", "—"))
    rows.sort(key=_sortkey)
    regime_line, regime_drift = regime_and_drift(scan_dir)

    out = [f"# A股扫描 v2 · Buy-List & 漏斗 — {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排(GBDT)→精排→研究→整合。L0/L1/L2 确定性,L3/L4 Claude 为引擎,"
           "**仅供研究,非投资建议。**_\n"]
    if regime_line:
        out.append(regime_line + "\n")

    # ── 市场研判(首席策略师视角;策略师未写 → 回退确定性脉搏)──
    mv = _load_market_view(scan_dir)
    if mv:
        from autoresearch.scan.market import render_funnel_readout  # lazy:避免 import cycle
        out += ["## 📈 今日 A 股市场(首席策略师视角)\n", mv, ""]
        readout = render_funnel_readout(scan_dir)
        if readout:
            out += [readout]
    else:
        from autoresearch.scan.market import market_pack, render_fallback_pulse
        pulse = render_fallback_pulse(market_pack(scan_dir))
        if pulse:
            out += ["## 📈 今日 A 股市场\n", pulse, ""]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗(数量)"] + _funnel_rows(meta, len(keep) or "?", len(finals), len(rows)) + [""]

    # ── 2. 各阶段卡点 + 概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, f"GBDT 学习重排({meta.get('l2_engine', 'gbdt')});信号弱/陷阱因子自动降权,零 LLM。")
    from autoresearch.scan.menu import menu_health  # lazy:确定性菜单体检,缺 staging 自 ""
    mh = menu_health(scan_dir)
    if mh:
        out += ["", mh]
    out += ["", "**精排(L3)入选(含论点/风险/催化)**:"]
    if finals:
        for fr in finals[:15]:
            out.append(f"- **{fr.get('name', '')}({fr.get('code', '')})** · {fr.get('sector', '')} — "
                       f"多头:{_strip(fr.get('thesis', ''))};风险:{_strip(fr.get('risk', ''))};"
                       f"催化:{_strip(fr.get('catalyst', ''))}")
    else:
        out.append("_无 finalists.csv_")
    out.append("")

    # ── 3. 投资建议 ──(vmap 已在上方加载并折回评级)
    vcol, vsep = (" 🛡️红队 |", "---|") if vmap else ("", "")
    n_l1 = meta.get("after_gate_a") or meta.get("universe") or len(l1_full) or "?"
    n_l2 = meta.get("l2_n") or len(l2_top) or "?"
    ch_map = {c: (r.get("recall_channels") or "") for c, r in l2_top.items()}   # 命中队列(随 keep 流到 L2 表)
    out += [f"## 3. 投资建议(buy-list, {len(rows)} 只,按 评级 → 确信度 排序;逐阶段结论)\n",
            f"| # | 名称 | 板块 | L1召回(#/{n_l1}) | L2粗排(#/{n_l2}) | L3精排 | L4研究·结论 | 评级 | 目标(EV) | 置信度 |" + vcol,
            "|---|---|---|---|---|---|---|---|---|---|" + vsep]
    for i, r in enumerate(rows, 1):
        code = str(r.get("code", "")).zfill(6)
        vcell = f" {_verify_badge(code, vmap)} |" if vmap else ""
        l3txt = _strip(r.get("thesis") or r.get("triage_reason", ""))
        conv = r.get("conviction")
        l3cell = l3txt + (f"·conv{conv}" if conv else "")
        out.append(
            f"| {i} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| {_l1_cell(code, l1_full, ch_map)} | {_l2_cell(code, l2_top)} | {l3cell} "
            f"| {_strip(r.get('l4', '—'))} "
            f"| **{r.get('rating', '—')}** | {r.get('target', '—')} | {r.get('conf', '—')} |" + vcell)
    out.append(f"\n_列注:**L1召回** #复合分名次/{n_l1}·命中队列(越小越强;低复合分票靠某条队列召回→名次很大);"
               f"**L2粗排** #GBDT重排名次/{n_l2}·gbdt分;**L3精排** = Opus holistic 论点 + conviction;"
               f"**L4研究·结论** = 决策卡深核后的关键定级依据(≥OW 取多头驱动,否则取空头/早停因)。_")
    out += _verify_detail(vmap)
    ws = scan_dir / "watchlist_status.csv"
    if ws.exists():
        import pandas as pd  # lazy:assemble 主体走 csv/json,仅此块用 pandas

        from autoresearch.scan.watchlist import render_watchlist_block
        wb = render_watchlist_block(pd.read_csv(ws, dtype={"code": str}))
        if wb:
            out += ["", wb]
    out += ["", "### 组合视角", _portfolio_note(rows), ""]
    kn = _knowledge_note(rows)
    if kn:
        out += [kn]
    out += _stage_token_estimate(scan_dir)
    out += ["## 诚实局限",
            "- 召回/粗排为启发式 + T+1 单 horizon IC 校准/训练(L1 复合分、L2 GBDT 同口径),随 regime 漂移;L3/L4 为 Claude 推理产出。",
            "- 业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
            "- A股涨跌停/停牌使名义止损未必可执行(见各决策卡执行段)。",
            f"\n_明细 + 漏斗溯源:`reports/scan/{folder}/`(summary.md + details/〈名称〉.md + trace/;目录名=运行时刻,数据日见 manifest.json)_"]
    body = "\n".join(out)
    banner = _self_review_banner(scan_dir, rows, body, regime_drift=regime_drift)   # UZI self-review 硬门:fail 顶到最前
    return f"{banner}\n{body}" if banner else body


# ───────────────────────── 发布 ─────────────────────────


def _safe_name(name: str) -> str:
    """股票名称 → 文件名安全(去 / \\ : * ? " < > | 与空白,*ST→ST);空则回退 未命名。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(name)).strip() or "未命名"


def _publish_details(scan_dir: Path, detail_out: Path) -> int:
    """把 L4 staging 决策卡发布到 details/,文件名用**股票名称**(非 ticker);只发当前 finalists。

    staging 卡仍以 <code>.md 暂存(parse_rating/retro 内部按 code);发布层改名 <名称>.md 便于人读。
    """
    src = scan_dir / "details"
    if not src.is_dir():
        return 0
    n = 0
    for fr in _read_csv(scan_dir / "finalists.csv"):
        code = str(fr.get("code", "")).zfill(6)
        card = src / f"{code}.md"
        if not card.exists():
            continue
        name = _safe_name(fr.get("name", "")) or code
        dst = detail_out / f"{name}.md"
        if dst.exists():                       # 同名兜底:挂 code 避免覆盖
            dst = detail_out / f"{name}_{code}.md"
        shutil.copy2(card, dst)
        n += 1
    return n


def _funnel_md(scan_dir: Path, analysis_date: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    lines = [f"# 漏斗溯源 — {analysis_date}\n", "六段:选集→召回→粗排(GBDT)→精排→研究→整合。\n"]
    lines += _funnel_rows(meta, len(keep) or "?", len(finals), len(finals))
    lines += ["", f"权重来源:{meta.get('weights_source', '?')};L2 引擎:{meta.get('l2_engine', '?')};"
              f"universe 源:{meta.get('source', '?')}。",
              "各阶段明细见同目录 CSV(L1_recall_top1000 / L2_gbdt_top200 / L3_fine_finalists)。"]
    return "\n".join(lines)


def _archive_reasoning(scan_dir: Path, pdir: Path) -> int:
    """把各阶段 LLM 中间推理件(prompt/批表/keep-judged/calib)归档到
    trace/reasoning/{l2,l3,l4}/,让发布报告自带可追溯的 LLM 输入;缺失静默跳过。"""
    routes = [
        # L2 已下沉确定性(GBDT),无 LLM 推理件;L3 holistic 选股 + L4 级联 + Tier-3 验证留痕。
        ("l3", lambda n: n.startswith("_l3")),
        ("l4", lambda n: n.startswith("_l4")),       # 含 _l4_tier2_<code>.md(Tier-2 复核稿)
        ("verify", lambda n: n.startswith("_v_") or n == "verify.csv"),  # Tier-3 买单对抗验证
    ]
    n = 0
    for stage, match in routes:
        for p in sorted(scan_dir.glob("*")):
            if p.is_file() and match(p.name):
                dst = pdir / "reasoning" / stage
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / p.name)
                n += 1
    return n


def _publish_pipeline(scan_dir: Path, out_base: Path, analysis_date: str) -> int:
    """把各阶段 staging 产物发布到 <YYYYMMDD_HHMM>/trace/(漏斗溯源 + reasoning 推理留痕)。"""
    pdir = out_base / "trace"
    pdir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "meta.json": "L0_universe_meta.json",
        "run_health.json": "run_health.json",              # 运行体检(NaN 降级/churn/L4 阶段效能)
        "weights_used.json": "weights_used.json",          # 重放快照(当日实际权重)
        "L1_scored_full.csv": "L1_scored_full.csv",        # 全量打分(所有过门股 sorted + recalled 标记)
        "L1_recall_top1000.csv": "L1_recall_top1000.csv",  # 召回工作集(top N)
        "L2_gbdt_top200.csv": "L2_gbdt_top200.csv",        # 粗排:GBDT 学习重排 top N(确定性)
        "L3_judged_full.csv": "L3_judged_full.csv",        # 精排全量判断(holistic 通看 ~200,非仅 finalists)
        "finalists.csv": "L3_fine_finalists.csv",          # 精排最终入选(top N)
    }
    n = 0
    for src, dst in mapping.items():
        p = scan_dir / src
        if p.exists():
            shutil.copy2(p, pdir / dst)
            n += 1
    wp = Path("context/factor_lab/weights.json")
    if wp.exists():
        shutil.copy2(wp, pdir / "L1_weights.json")
        n += 1
    (pdir / "funnel.md").write_text(_funnel_md(scan_dir, analysis_date), encoding="utf-8")
    n += _archive_reasoning(scan_dir, pdir)
    return n + 1


def run(analysis_date: str, scan_dir: Path | None = None, out_root: Path | None = None,
        hhmm: str | None = None, run_date: str | None = None) -> Path:
    scan_dir = scan_dir or Path("context/scan") / analysis_date
    out_root = out_root or Path("reports/scan")
    now = datetime.now()
    hhmm = hhmm or now.strftime("%H%M")
    # 发布目录时间戳 = **实际运行时刻**(run_date 仅自测注入);数据日 analysis_date 另记 manifest,与目录名解耦
    run_compact = (run_date or now.strftime("%Y-%m-%d")).replace("-", "")
    folder = f"{run_compact}_{hhmm}"
    out_base = out_root / folder                       # reports/scan/<运行日YYYYMMDD>_<HHMM>/
    detail_out = out_base / "details"
    detail_out.mkdir(parents=True, exist_ok=True)
    n_cards = _publish_details(scan_dir, detail_out)
    import contextlib

    from autoresearch.scan import health as _health  # lazy:体检失败不阻发布
    with contextlib.suppress(Exception):
        _health.write_run_health(scan_dir)             # 先写 staging,再随 trace mapping 带走
    n_pipe = _publish_pipeline(scan_dir, out_base, analysis_date)   # trace/ 挂 out_base(details 同级)
    (out_base / "manifest.json").write_text(json.dumps(            # retro 按 analysis_date 定位本报告(目录名≠数据日)
        {"analysis_date": analysis_date, "generated_at": now.isoformat(timespec="seconds"), "hhmm": hhmm},
        ensure_ascii=False), encoding="utf-8")
    md = build_summary(scan_dir, analysis_date, hhmm, folder)
    summary_path = out_base / "summary.md"
    summary_path.write_text(md, encoding="utf-8")
    with contextlib.suppress(Exception):               # 现场导航页(第二天复盘入口)
        (out_base / "index.md").write_text(_health.index_md(scan_dir, out_base), encoding="utf-8")
    print(f"[L5 整合] summary → {summary_path}  (数据日 {analysis_date})")
    print(f"[L5 整合] details → {detail_out}  ({n_cards} 张卡 + trace/ {n_pipe} 件溯源)")
    return summary_path


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L5 整合(漏斗 + 三段 summary + trace/)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    args = ap.parse_args()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
