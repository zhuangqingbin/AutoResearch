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

纯确定性(stdlib + parse_rating),零 LLM。

用法:
  uv run --no-sync python scripts/assemble_scan.py 2026-06-20
  uv run --no-sync python scripts/assemble_scan.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import tempfile
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


# ───────────────────────── 解析 helpers ─────────────────────────


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _strip(s: str | None) -> str:
    return (s or "").replace("**", "").strip()


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
        return {**fr, "rating": "—", "target": "⚠️卡片缺失", "rr": "—", "proposal": "—", "conf": "—"}
    dash = _parse_dashboard(text)
    conf = _get(dash, "置信度")
    if not conf:
        m = _CONF_RE.search(text)
        conf = m.group(1) if m else "—"
    prop = _PROPOSAL_RE.search(text)
    rub = _RUBRIC_RE.search(text)
    return {
        **fr,
        "rating": parse_rating(text),
        "target": _get(dash, "EV目标", "目标") or "—",
        "rr": _get(dash, "R:R") or "—",
        "proposal": prop.group(1).upper() if prop else "—",
        "conf": conf or "—",
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
        f"| L3 | 精排 | {n_l3} | Sonnet·holistic | 1 agent 通看 ~200 比较选 + 增量证据/论点/红队 |",
        f"| L4 | 研究 | {n_cards} 卡 | Sonnet+Opus | analyze-ticker-lite 决策卡 + Tier-2 条件平反 + Tier-3 证伪 |",
    ]


def _l1_cell(code: str, l1_full: dict[str, dict]) -> str:
    """L1 召回结论:#召回名次 · 复合分。"""
    r = l1_full.get(str(code).zfill(6))
    if not r:
        return "—"
    return f"#{r.get('rank', '?')}·{r.get('composite', '?')}"


def _l2_cell(code: str, l2_top: dict[str, dict]) -> str:
    """L2 粗排结论:#重排名次(· gbdt 分,若 GBDT 启用)。"""
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
    l4t2 = list(det.glob("_l4_tier2_*"))
    verify = list(det.glob("_v_*"))
    rows = [
        ("L0 选集", "确定性", 0, 0, "纯 pandas 硬门"),
        ("L1 召回", "确定性", 0, 0, "复合分排序"),
        ("L2 粗排", "确定性·GBDT", 0, 0, "LightGBM 重排,零 LLM"),
        ("L3 精排", "Sonnet·holistic", 1 if l3 else 0, _b(l3), "1 agent 通看 ~200 选 30"),
        ("L4·T1 研究", "Sonnet", len(cards), _b(cards) + _b(l4t1), f"{len(cards)} 张决策卡"),
        ("L4·T2 平反", "Opus", len(l4t2), _b(l4t2), "条件触发(高conv趋势假阴)"),
        ("L4·T3 验证", "Opus", len(verify), _b(verify), "买点多空辩论证伪"),
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


def _self_review_banner(scan_dir: Path, rows: list[dict], summary_text: str) -> str:
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
           "summary_text": summary_text, "lessons": lessons}
    return self_review.render_banner(self_review.review(ctx))


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

    out = [f"# A股扫描 v2 · Buy-List & 漏斗 — {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排(GBDT)→精排→研究→整合。L0/L1/L2 确定性,L3/L4 Claude 为引擎,"
           "**仅供研究,非投资建议。**_\n"]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗(数量)"] + _funnel_rows(meta, len(keep) or "?", len(finals), len(rows)) + [""]

    # ── 2. 各阶段卡点 + 概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, f"GBDT 学习重排({meta.get('l2_engine', 'gbdt')});信号弱/陷阱因子自动降权,零 LLM。")
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
    out += [f"## 3. 投资建议(buy-list, {len(rows)} 只,按 评级 → 确信度 排序;逐阶段结论)\n",
            "| # | 名称 | 板块 | L1召回 | L2粗排 | L3论点·确信 | 评级 | 目标(EV) | 置信度 |" + vcol,
            "|---|---|---|---|---|---|---|---|---|" + vsep]
    for i, r in enumerate(rows, 1):
        code = str(r.get("code", "")).zfill(6)
        vcell = f" {_verify_badge(code, vmap)} |" if vmap else ""
        l3txt = _strip(r.get("thesis") or r.get("triage_reason", ""))
        conv = r.get("conviction")
        l3cell = l3txt + (f"·conv{conv}" if conv else "")
        out.append(
            f"| {i} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| {_l1_cell(code, l1_full)} | {_l2_cell(code, l2_top)} | {l3cell} "
            f"| **{r.get('rating', '—')}** | {r.get('target', '—')} | {r.get('conf', '—')} |" + vcell)
    out += _verify_detail(vmap)
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
    banner = _self_review_banner(scan_dir, rows, body)   # UZI self-review 硬门:fail 顶到最前
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
    n_pipe = _publish_pipeline(scan_dir, out_base, analysis_date)   # trace/ 挂 out_base(details 同级)
    (out_base / "manifest.json").write_text(json.dumps(            # retro 按 analysis_date 定位本报告(目录名≠数据日)
        {"analysis_date": analysis_date, "generated_at": now.isoformat(timespec="seconds"), "hhmm": hhmm},
        ensure_ascii=False), encoding="utf-8")
    md = build_summary(scan_dir, analysis_date, hhmm, folder)
    summary_path = out_base / "summary.md"
    summary_path.write_text(md, encoding="utf-8")
    print(f"[L5 整合] summary → {summary_path}  (数据日 {analysis_date})")
    print(f"[L5 整合] details → {detail_out}  ({n_cards} 张卡 + trace/ {n_pipe} 件溯源)")
    return summary_path


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = "2026-06-20"
        scan = root / "context/scan" / d
        (scan / "details").mkdir(parents=True)
        (scan / "meta.json").write_text(json.dumps({
            "universe": 5483, "recall_n": 1000, "l2_n": 200, "l2_engine": "gbdt", "source": "tushare",
            "weights_source": "factor_lab.calibrate"}), encoding="utf-8")
        # L1 召回(概览用)
        with (scan / "L1_recall_top1000.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "composite"])
            w.writeheader()
            for i in range(20):
                w.writerow({"code": f"{300000 + i:06d}", "name": f"光{i}", "industry": "电子",
                            "composite": 90 - i})
        # L1 全量打分(_l1_cell 查 rank/composite)+ L2 粗排(GBDT 学习重排;_l2_cell 查 l2_rank/gbdt)
        l1l2 = [("300476", 5, 80, 2, 0.54), ("600519", 50, 60, 40, 0.49),
                ("002384", 8, 77, 6, 0.52), ("301117", 3, 82, 1, 0.55)]
        with (scan / "L1_scored_full.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["rank", "recalled", "code", "name", "industry", "composite",
                                              "winner_rate", "pct_60d", "rsi6"])
            w.writeheader()
            for code, rk, comp, _lr, _g in l1l2:
                w.writerow({"rank": rk, "recalled": True, "code": code, "name": code, "industry": "电子",
                            "composite": comp, "winner_rate": 60, "pct_60d": 80, "rsi6": 65})
        with (scan / "L2_gbdt_top200.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["l2_rank", "gbdt_score", "code", "name", "industry", "composite"])
            w.writeheader()
            for code, _rk, comp, lr, g in l1l2:
                w.writerow({"l2_rank": lr, "gbdt_score": g, "code": code, "name": code,
                            "industry": "电子", "composite": comp})
        # L3 精排 finalists(带 thesis/risk/catalyst)
        with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses", "conviction",
                                              "triage_lean", "triage_reason", "thesis", "risk", "catalyst"])
            w.writeheader()
            w.writerow({"ticker": "300476", "code": "300476", "name": "甲", "sector": "光模块",
                        "lenses": "动量", "conviction": "203", "triage_lean": "看多", "triage_reason": "加速",
                        "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"})
            w.writerow({"ticker": "600519", "code": "600519", "name": "乙", "sector": "白酒",
                        "lenses": "价值", "conviction": "125", "triage_lean": "中性", "triage_reason": "低估",
                        "thesis": "现金牛低估", "risk": "需求弱", "catalyst": "中报"})
            w.writerow({"ticker": "002384", "code": "002384", "name": "丙", "sector": "光模块",
                        "lenses": "动量", "conviction": "118", "triage_lean": "回避", "triage_reason": "过热",
                        "thesis": "x", "risk": "y", "catalyst": "z"})
            w.writerow({"ticker": "301117", "code": "301117", "name": "丁", "sector": "光模块",
                        "lenses": "动量", "conviction": "150", "triage_lean": "看多", "triage_reason": "稳健",
                        "thesis": "维持OW对照票", "risk": "无硬伤", "catalyst": "无"})
        # L4 决策卡(002384 故意缺卡 → 测降级;301117 OW+维持 对照不折回)
        for tk, rating, prop, rub in [("300476", "Overweight", "BUY", "Overweight"),
                                      ("600519", "Hold", "HOLD", "Hold"),
                                      ("301117", "Overweight", "BUY", "Overweight")]:
            (scan / "details" / f"{tk}.md").write_text(
                "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
                f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n"
                f"**Rubric建议**: {rub}(净分 +2,OW门 3/3)\n\n**Rating**: {rating}\n\n"
                f"FINAL TRANSACTION PROPOSAL: **{prop}**\n", encoding="utf-8")
        # 中间推理件(应归档到 trace/reasoning/{l3,l4};L2 已确定性化,无 LLM 留痕)
        for fn in ("_l3_judged_0.csv", "_l4_prompt.md", "_l4_batch_0.md"):
            (scan / fn).write_text("x", encoding="utf-8")
        # Tier-3 多空辩论:买单 300476 降级(带 bull+consensus)+ 多空中间稿(→ reasoning/verify/)
        (scan / "verify.csv").write_text(
            "code,verdict,bull,bear,trigger,consensus\n"
            '300476,降级,"AI光模块需求真切","估值已透支PE160","跌破120元","降级2/3(估值/资金)"\n'
            '301117,维持,"龙头卡位稀缺","无硬伤","继续持有","维持3/3"\n', encoding="utf-8")
        (scan / "_v_bull_300476.md").write_text("多头研究员稿", encoding="utf-8")
        (scan / "_v_300476.md").write_text("空头研究员稿", encoding="utf-8")
        # 运行日(run_date)= 6-21 ≠ 数据日(d)= 6-20:验证目录名取运行时刻、数据日落 manifest(解耦)
        summary_path = run(d, scan_dir=scan, out_root=root / "reports/scan", hhmm="0930", run_date="2026-06-21")
        md = summary_path.read_text(encoding="utf-8")
        out_base = root / "reports/scan/20260621_0930"     # 目录名 = 运行日_HHMM(非数据日)
        if summary_path.parent != out_base:
            fails.append(f"发布目录应取运行日(20260621_0930),实得 {summary_path.parent.name}")
        mpath = out_base / "manifest.json"
        if not mpath.exists() or json.loads(mpath.read_text(encoding="utf-8")).get("analysis_date") != d:
            fails.append("manifest.json 缺 / analysis_date 应为数据日 d")
        pdir = out_base / "trace"
        for fn in ("L1_recall_top1000.csv", "L2_gbdt_top200.csv", "L3_fine_finalists.csv", "funnel.md",
                   "L0_universe_meta.json"):
            if not (pdir / fn).exists():
                fails.append(f"trace 缺 {fn}")
        if not (out_base / "summary.md").exists():
            fails.append("summary.md 未发布到 <运行日_HHMM>/")
        if not (out_base / "details" / "甲.md").exists():        # 决策卡按名称发布(300476→甲)
            fails.append("决策卡未按名称发布(details/甲.md)")
        if (out_base / "details" / "300476.md").exists():
            fails.append("发布层不应再用 ticker.md")
        rdir = pdir / "reasoning"
        for stage, fn in [("l3", "_l3_judged_0.csv"), ("l4", "_l4_prompt.md"), ("l4", "_l4_batch_0.md"),
                          ("verify", "verify.csv"), ("verify", "_v_300476.md")]:
            if not (rdir / stage / fn).exists():
                fails.append(f"reasoning 归档缺 {stage}/{fn}")

    for must in ["## 1. 漏斗", "## 2. 各阶段", "## 3. 投资建议", "5483", "1000", "选集", "召回", "粗排",
                 "精排", "Overweight", "+30%", "⚠️卡片缺失", "AI 光模块需求超预期", "组合视角",
                 "L1召回", "L2粗排", "L3论点·确信", "#5·80", "·g0.54",       # 逐阶段结论列
                 "## 各阶段 token 消耗", "确定性·GBDT",                       # token 估算段
                 "🛡️红队", "🛡️ Tier-3 买单多空辩论", "⚠️降级", "✅维持", "估值已透支PE160",
                 "AI光模块需求真切", "降级2/3"]:
        if must not in md:
            fails.append(f"summary 缺 '{must}'")
    # buy-list 排序(评级→确信度):丁(OW维持)<甲(Hold降级,conv203)<乙(Hold,conv125)<丙(缺卡)
    s3 = md.find("## 3. 投资建议")
    ords = [md.find(n, s3) for n in ("丁", "甲", "乙", "丙")]
    if not (ords[0] < ords[1] < ords[2] < ords[3]):
        fails.append(f"buy-list 排序错(应 丁<甲<乙<丙): {ords}")
    # Tier-3 折回评级:300476(甲,降级)OW→Hold 踢出买单;301117(丁,维持)留 OW(代码列已删,按名称定位行)
    row476 = next((ln for ln in md.splitlines() if "甲" in ln and ln.lstrip().startswith("|")), "")
    if "Overweight" in row476 or "Hold" not in row476:
        fails.append(f"降级未折回(甲/300476 应 OW→Hold): {row476}")
    row117 = next((ln for ln in md.splitlines() if "丁" in ln and ln.lstrip().startswith("|")), "")
    if "Overweight" not in row117:
        fails.append(f"维持不应改评级(丁/301117 应留 OW): {row117}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  L5 三段(漏斗/各阶段概览/投资建议)+ trace/ 发布(目录名=运行日 + manifest 数据日)"
          "+ reasoning 留痕(含 verify)+ Tier-3 多空辩论徽标(多/空/共识)+ C·rubric 解析 + 缺卡降级 + 排序 全过")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L5 整合(漏斗 + 三段 summary + trace/)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--selftest", action="store_true", help="离线验证解析/排序逻辑(无网络)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
