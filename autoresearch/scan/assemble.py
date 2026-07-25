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
import contextlib
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


def _load_ensemble(scan_dir: Path) -> dict[str, dict]:
    """读买单复核 ensemble(B10 集成配方;task-11-brief——≥OW **新派**卡各追加 2 独立 run
    取中位)`_ensemble.json` → {code: {ratings, median, spread}}。

    两种产物布局都认(fb_20260714_003 起 L4 = 每股独立 workflow):
    · 旧批量:`_ensemble.json` = `[{"code","ratings":[...],"median":...,"spread":int}]`
    · 新每股:`_ensemble_<code>.json` = 单条 record(dict 或 单元素 list)——每股 workflow 各写
      各的文件,天然无并发写竞态;per-code 文件后读,同 code 覆盖旧批量文件。
    无文件/坏 json → {}(presence-gated,老路不破,同 `_load_verify` 惯例)。
    """
    out: dict[str, dict] = {}

    def _ingest(raw) -> None:
        for r in raw if isinstance(raw, list) else [raw]:
            if isinstance(r, dict) and r.get("code"):
                out[str(r["code"]).strip().zfill(6)] = r

    for p in [scan_dir / "_ensemble.json", *sorted(scan_dir.glob("_ensemble_*.json"))]:
        if not p.exists():
            continue
        try:
            _ingest(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — 可选层,坏 json 不挡整份报告发布
            continue
    return out


def _ensemble_flag(rec: dict | None) -> bool:
    """🎭 人裁条件:3 run 分歧 ≥2 档;或复核 run 缺席退化(degraded,N<3)且仍有分歧(spread>0)——
    degraded(复核 run 不齐)时 workflow 与本侧均不折回,仅 ens_flag 强制人裁展示,1 档分歧
    也不许静默消失(T9-11 review Important#2)。"""
    if not rec:
        return False
    spread = int(rec.get("spread") or 0)
    return spread >= 2 or (bool(rec.get("degraded")) and spread > 0)


def _apply_ensemble_fold(rating: str, rec: dict | None) -> str:
    """复核折回:ow_review(默认)只向下(更靠 Sell)折;sell_review 只向温和折(救误卖持仓,
    Wave1 ⑤-3)。degraded(复核 run 不齐)→ 原样不折,交 ens_flag 人裁。
    median/rating 不在五档词表(脏数据)→ 原样不动,不报错。
    """
    if not rec or rec.get("degraded"):
        return rating
    median = rec.get("median")
    if median not in TIER_RANK or rating not in TIER_RANK:
        return rating
    if rec.get("trigger") == "sell_review":
        return median if TIER_RANK[median] < TIER_RANK[rating] else rating
    return median if TIER_RANK[median] > TIER_RANK[rating] else rating


def _ensemble_dissent_lines(emap: dict[str, dict]) -> list[str]:
    """买单复核 spread≥2(3 run 评级分歧 ≥2 档)→ 组合视角节人裁提示行;无分歧 → []。"""
    lines = []
    for code, e in sorted(emap.items()):
        if _ensemble_flag(e):
            ratings = e.get("ratings") or []
            lines.append(f"🎭 买单复核分歧:{code} {len(ratings)} run={ratings},已按中位折回,建议人工复核")
    return lines


def _dump_final_ratings(scan_dir: Path, rows: list[dict]) -> None:
    """P0-2(坏账③修复):把 ensemble/verify 折回后的**终评级**落 `<scan_dir>/_final_ratings.json`
    (`{code: rating}`)。

    此前 retro 归因只读发布报告 `details/*.md` 的**卡面**评级(`parse_rating`)——Tier-3 红队
    降级/否决 + 买单 ensemble 折回都只改了 `build_summary` 内存里的 `rows["rating"]`,从未写回
    卡片文件,导致被折回的 OW(如 06-30 胜宏)仍以卡面 OW 进 attribution,污染 `bought`/评级基率
    (STAGES.md 开放线头 #6)。`retro._buylist` 优先 join 本文件(presence-gated,缺文件回退卡面
    解析,老路不破)。调用时机:两个 fold 循环(verify 降级 + ensemble 折回)都已跑完、`rows.sort`
    之前——此时 `rows` 里的 `rating` 即最终值。IO 失败不阻发布(同 assemble 其余 staging 写手惯例)。
    """
    import contextlib
    with contextlib.suppress(Exception):
        out = {str(r.get("code", "")).zfill(6): r.get("rating", "—")
               for r in rows if r.get("code")}
        (Path(scan_dir) / "_final_ratings.json").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8")
    with contextlib.suppress(Exception):   # Wave5 ②C:早停分桶落盘(0买真机制记账,独立文件
        write_early_stop(scan_dir)         # 不动 _final_ratings.json 的 {code: rating} 契约)


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
    否则取空头/早停因(为何没给买点)。回退满卡多空对撞首条空。
    宽 96(0买日『为何没买』是该表全部信息量,48 腰斩到句中,07-04 用户反馈)。"""
    m = _BULLBEAR_RE.search(text)
    if m:
        segs = re.split(r"[｜|]", m.group(1))
        want = "多" if rating in ("Buy", "Overweight") else "空"
        seg = next((s for s in segs if s.strip().startswith(want)), "")
        if seg:
            return _clip(seg.strip().lstrip("多空").strip(" :："), 96)
    m = _STOPWHY_RE.search(text)
    if m:
        return _clip(m.group(1), 96)
    m = _BEARBULLET_RE.search(text)
    if m:
        return _clip(m.group(1), 96)
    return "—"


def _decision_text(scan_dir: Path, ticker: str) -> str | None:
    """定位 finalist 的 lite 决策卡:context/scan/<date>/details/<ticker>.md,按 6 位代码 glob 兜底。"""
    base = scan_dir / "details"
    code = ticker.split(".")[0]
    tries = [base / f"{ticker}.md"]
    if base.is_dir():
        # 上游 CSV 往返可能吃掉前导零(2156 ← 002156);6 位零填后再 glob 一次兜底。
        for c in dict.fromkeys((code, code.zfill(6))):
            tries += sorted(p for p in base.glob(f"{c}*.md"))
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


_GATES3 = ("主力真在", "业绩真兑现", "估值不透支")
_GATESEG_RE = re.compile(r"OW三门[^\n→]*")


def _parse_gate_seg(seg: str) -> dict[str, bool]:
    """单段『OW三门…』文本 → {门: 是否✗};门名允许紧邻「门」后缀 + 空白再判标记,找不到判 False
    (gate_status 的既有语义,供其挑出目标段后调用)。"""
    out: dict[str, bool] = {}
    for g in _GATES3:
        i = seg.find(g)
        if i < 0:
            continue
        j = i + len(g)
        if seg[j:j + 1] == "门":                # 措辞容错:「主力真在门✗」
            j += 1
        while seg[j:j + 1].isspace():           # 措辞容错:门名与标记之间的空格(l4-card.md Rubric 行写法)
            j += 1
        out[g] = seg[j:j + 1] == "✗"
    return out


def _seg_has_mark(seg: str) -> bool:
    """段内是否至少一个门名紧邻(容许「门」后缀 + 空白)着实际 ✓/✗ 标记——用来判该段是否「可解析」。"""
    for g in _GATES3:
        i = seg.find(g)
        if i < 0:
            continue
        j = i + len(g)
        if seg[j:j + 1] == "门":
            j += 1
        while seg[j:j + 1].isspace():
            j += 1
        if seg[j:j + 1] in ("✓", "✗"):
            return True
    return False


def gate_status(text: str) -> dict[str, bool] | None:
    """解析卡文『OW三门…』段 → {门: 是否✗失守};无门柱段(如早停卡)→ None。
    门柱直方图与 learning.cross_calib 共用本函数(单一口径,防漂移)。

    容错(漏斗 P0+P1 波 Task 2b 修复):①门名与 ✓/✗ 之间允许空白(l4-card.md 满卡模板 Rubric 行的
    真实写法「主力真在 ✗」带空格);②卡片正文可能多处出现"OW三门"字样(如先散文一句带过、文末
    Rubric 行才结构化判定)——取全部匹配段里**最后一个**能解析出至少一个 ✓/✗ 标记的段;若全部段
    都解析不出标记,退回首段(与改动前完全一致的返回语义)。"""
    matches = list(_GATESEG_RE.finditer(text))
    if not matches:
        return None
    for m in reversed(matches):
        seg = m.group(0)
        if _seg_has_mark(seg):
            return _parse_gate_seg(seg)
    return _parse_gate_seg(matches[0].group(0))


_EARLYSTOP_RE = re.compile(r"\*\*早停\*\*[:：]\s*停于\s*(P[0-9])\s*[｜|]\s*停因[:：]\s*([^\s｜|]+)")
_STOP_REASONS = ("数据不足", "涨停追高", "题材透支", "资金流出",
                 "估值透支", "基本面恶化", "其他")


def parse_early_stop(text: str) -> dict | None:
    """决策卡的机读早停行 → {"phase","reason"};满卡无此行 → None。

    Wave5 ②C:0 买的真机制是早停(07-21 实测 12 卡 6 张早停),而早停卡按定义不写 OW三门段
    —— 门直方图看不见它们,账本也从来没数过。枚举外的自由文本归入「其他」(不丢样本、
    也不让写卡人用自造词绕开分桶)。
    """
    m = _EARLYSTOP_RE.search(text or "")
    if not m:
        return None
    reason = m.group(2).strip()
    return {"phase": m.group(1), "reason": reason if reason in _STOP_REASONS else "其他"}


def write_early_stop(scan_dir: Path | str) -> dict[str, dict]:
    """逐卡解析早停行 → `_early_stop.json`({code: {phase,reason}});无早停卡则写空对象。"""
    scan_dir = Path(scan_dir)
    out: dict[str, dict] = {}
    base = scan_dir / "details"
    if base.is_dir():
        for p in sorted(base.glob("*.md")):
            got = parse_early_stop(p.read_text(encoding="utf-8"))
            if got:
                code = p.stem
                out[code.zfill(6) if code.isdigit() else code] = got
    (scan_dir / "_early_stop.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def gate_histogram(scan_dir: Path, rows: list[dict]) -> str:
    """OW三门失守分布一行(确定性,逐卡数 `OW三门 …` 段的 ✗)。0买日一行看懂"今天为什么没买"
    ——胜过读 30 格被截断的结论;有买日同样给出门柱形状。无可解析卡 → ''。"""
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
            f"(任一门✗ 即压 ≤Hold;门柱即当日 0买/有买的结构性原因)")


_gate_histogram = gate_histogram   # 向后兼容别名(Wave5 ①:render CLI 复用公共名)


def _sortkey(r: dict):
    tier = TIER_RANK.get(r.get("rating", ""), 99)
    try:
        conv = float(r.get("conviction") or 0)
    except ValueError:
        conv = 0.0
    return (tier, -conv)


# ───────────────────────── 三段 summary ─────────────────────────


def _funnel_rows(meta: dict, n_l2, n_l3, n_cards, n_pinned: int = 0) -> list[str]:
    l2_eng = meta.get("l2_engine", "stratified")
    l3_out = f"{n_l3} (+{n_pinned} 保送直通)" if n_pinned else f"{n_l3}"   # 保送不占 L3 名额,单列标注
    return [
        "| 阶段 | 名称 | 出量 | 引擎 | 卡点标准 |", "|---|---|---:|---|---|",
        f"| L0 | 选集 | {meta.get('universe', '?')} | 确定性 | 全A {meta.get('universe_raw', '?')} → 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
        f"| L1 | 召回 | {meta.get('recall_n', '?')} | 确定性 | 轻门 + 行业条件化复合分(fwd_2_oc 超短主尺 IC 校准) top |",
        f"| L2 | 粗排 | {n_l2} | 分层采样/{l2_eng} | 确定性分层采样(sn_composite 排序+风格桶 floor+sector cap;零模型零 LLM;文件名/列名 gbdt 为遗留别名) |",
        f"| L3 | 精排 | {l3_out} | Opus·max·holistic | 1 agent 通看 ~200 比较选 + 增量证据/论点/红队(保送票不占名额) |",
        f"| L4 | 研究 | {n_cards} 卡 | Opus·medium | 一只=一个 Opus subagent 渐进深度 DD + 早停 |",
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
         "l4-intel 盲搜落稿(1 文件=1 sonnet 会话;网查计费经 OTEL,此处**未计非零**;未启用=0;不计入 LLM 调用合计)"),
        ("L4 新闻网查", "WebSearch", "—", "L4news", 0, 0,
         "P3 有界活体新闻(≤3/卡)+ sector/macro 网查(≤2)——无落盘 artifact,token 计费经 OTEL/`/usage`,此处**未计非零**"),
        ("整合 assemble", "确定性", "—", "assemble", 0, 0, "L5 组装 + self_review(截至本表渲染)"),
    ]
    lines = ["## 各阶段耗时 & token 消耗(估算)",
             "| 阶段 | 引擎 | effort | 墙钟 | LLM 调用 | 落盘字节 | ~token | 说明 |",
             "|---|---|---|---:|---:|---:|---:|---|"]
    tot_calls = tot_tok = 0
    for name, eng, eff, tkey, calls, b, note in rows:
        tok = int(b / _BYTES_PER_TOK)
        tot_calls += 0 if name.startswith("L4 输入") else calls
        tot_tok += tok
        lines.append(f"| {name} | {eng} | {eff} | {_wall(tkey)} | {calls or '—'} | {b or '—'} | {tok or '—'} | {note} |")
    lines.append(f"| **合计** | — | — | {_wall('总计')} | **{tot_calls}** | — | **~{tot_tok}** | 落盘可测下界(墙钟 = mtime 推导下界·stage_timing.py) |")
    if cards and not list(det.glob("_l4_prompt*")):
        lines += ["", "> ⚠️ L4 输入 prompt 未落稿(`_l4_prompt_*` 缺)——上表 L4 行仅计输出;派发前先 "
                  "`uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>` "
                  "落稿,输入侧才可计。"]
    lines += ["", "> 口径:**落盘字节 ÷ 2.8**(CJK 粗估)。**落稿契约**(playbook):编排把 L3 输入表落 "
              "`_l3_table.md`、每卡完整 prompt 落 `_l4_prompt_<code>.md` "
              "后,本表 ≈ **输入+输出全量下界**;缺稿段 `—` = 该段用量**未计而非为零**。"
              "另:每个 subagent 系统前缀 ~15k token(批内同前缀,prompt cache 摊薄)未计;"
              "真实计费口径只有 Claude Code `/usage` 可见。", ""]
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
    buys = [r for r in rows if r.get("rating") in ("Buy", "Overweight")]
    note = (f"买入/超配 **{len(buys)}** 只;板块集中度:{top or '—'}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,催化日历做节奏。")
    if len(buys) >= 2:                       # 买单同板块 = 1 个 bet 不是 N 个(组合视角告警)
        bsec = Counter((r.get("sector") or r.get("industry") or "?") for r in buys)
        k, v = bsec.most_common(1)[0]
        if v >= 2:
            note += f" **⚠️ {v}/{len(buys)} 只买单同属{k} = 相关性上是 1 个 bet,仓位按 1 个算。**"
    return note


def _position_overlay(scan_dir: Path, rows: list[dict]) -> str:
    """仓位建议(组合 overlay,确定性):regime 档位 + 菜单病取下沿 + 0 买一致性。缺 regime → ""。

    只作用于总仓位,不改单票评级(与策略师"方向只进 L5"同一铁律)。
    """
    try:
        meta = _load_json(scan_dir / "meta.json")
        regime = meta.get("regime")
    except Exception:  # noqa: BLE001
        return ""
    band = {"risk_off": "0–2 成", "range": "3–5 成", "trend": "5–8 成"}.get(regime or "")
    if not band:
        return ""
    n_buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    sick = ""
    try:
        from autoresearch.scan.menu import l4_budget
        n, _why = l4_budget(scan_dir)
        if n < 30:
            sick = "(菜单病 → 取区间下沿)"
    except Exception:  # noqa: BLE001
        pass
    tail = ("今日 0 买 → 空仓/底仓与系统读数一致,别为凑单加仓。" if n_buys == 0
            else f"{n_buys} 只买单在区间内按评级×置信度分配。")
    return (f"**仓位建议(overlay,非个股)**:regime={regime} → 总仓位基准 **{band}**{sick};{tail}")


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


def _pinned_section(scan_dir: Path, analysis_date: str, pinned_rows: list[dict],
                    l1_full: dict, l2_top: dict, ch_map: dict, vmap: dict, n_l1, n_l2,
                    pinned_path: str | Path | None = None) -> str:
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
    lines = ["## 📌 保送持仓(用户手工直通;L1→L5 全程强留,**不占 L3 名额**,与真实精选分列)", "",
             "_这些是你手工保送的持仓/关注票,不是漏斗筛出来的买入候选;评级仍按各自 rubric 独立"
             "判定,不因『保送』放松尽调。_", ""]
    if pinned_rows:
        lines += _buylist_table_lines(pinned_rows, l1_full, l2_top, ch_map, vmap,
                                      n_l1, n_l2, note_col=True)
    else:
        lines += ["_本次运行内无强留的保送持仓(finalists 无 lane=pinned 行)。_"]
    if expired:
        lines += ["", "_已过期(不再强留,续期请更新 pinned.jsonc):_"]
        for e in expired:
            note = f" ——{e['note']}" if e.get("note") else ""
            lines.append(f"- {e['code']}{note}(已于 {e.get('expires', '—')} 过期)")
    return "\n".join(lines)


def _sector_view_section(scan_dir: Path) -> str:
    """行业 brief 研判段汇总(Phase 3;方向性内容只在整合层——地形段已注 L3/L4)。无 briefs → ''。"""
    d = scan_dir / "sector_briefs"
    if not d.is_dir():
        return ""
    try:
        from autoresearch.sector.brief import extract_view, parse_direction
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    for p in sorted(d.glob("*.md")):
        try:
            view = extract_view(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if view:
            parts.append(f"**{p.stem}**(方向:{parse_direction(view) or '—'})\n\n{view}")
    if not parts:
        return ""
    return "## 🏭 行业研判(sector-research lite · 仅整合层)\n\n" + "\n\n".join(parts)


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
                       "main_net_ratio": lf.get("main_net_ratio"),
                       "rubric_suggest": r.get("rubric_suggest"), "rubric_dev": r.get("rubric_dev")})
    n_present = sum(1 for r in rows if r.get("target") != "⚠️卡片缺失")
    lessons = []
    try:
        import autoresearch.learning.feedback_store as fs
        lessons = fs.lessons_for([("global", "*")])
    except Exception:  # noqa: BLE001
        pass
    ctx = {"finalists": finals, "n_cards_expected": len(rows), "n_cards_present": n_present,
           "summary_text": summary_text, "lessons": lessons, "regime_drift": regime_drift,
           "flow": {                                       # 编排完备性 lint(LLM 段可能被静默跳过)
               "buys_n": sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight")),
               "verify_n": len(_load_verify(scan_dir)),
               "has_market_view": (scan_dir / "market_view.md").exists(),
               "finalists_n": len(rows)}}
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
    with contextlib.suppress(Exception):                            # 产物形状 lint(线C 2026-07-18;全 warn/info 起步)
        shape_extra = self_review.product_shape_lint(scan_dir, scan_dir.name)
        if shape_extra:
            res["failures"].extend(shape_extra)
            res["n_warn"] = res.get("n_warn", 0) + sum(1 for x in shape_extra if x.get("severity") == "warn")
    with contextlib.suppress(Exception):
        self_review.dump_gate_fires(scan_dir, res, scan_dir.name)   # R3 留痕;IO 失败不阻发布
    with contextlib.suppress(Exception):
        self_review.dump_ow_gate_fires(scan_dir)          # OW三门失守 binding 行;IO 失败不阻发布
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


def build_summary(scan_dir: Path, analysis_date: str, hhmm: str, folder: str,
                  pinned_path: str | Path | None = None) -> str:
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
    emap = _load_ensemble(scan_dir)  # 买单复核 ensemble(B10 集成配方,≥OW 追加 2 独立 run 取中位);无 _ensemble.json 则空(老路不破)
    for r in rows:
        e = emap.get(str(r.get("code", "")).zfill(6))
        if not e:
            continue
        folded = _apply_ensemble_fold(r.get("rating", "Hold"), e)
        if folded != r.get("rating"):
            r["rating"] = folded
            r["proposal"] = _PROPOSAL_BY_RATING.get(folded, r.get("proposal", "—"))
        if _ensemble_flag(e):
            r["ens_flag"] = True                # 🎭复核分歧:spread≥2 → 行 badge + 组合视角人裁提示
    _dump_final_ratings(scan_dir, rows)   # P0-2:两个 fold 循环已跑完 → rows["rating"] 即终评级,落盘供 retro 优先 join(含保送,retro 口径不变)
    rows.sort(key=_sortkey)
    # ── feedback fb_20260714_001:保送(lane==pinned)与真实精选分列 ──
    # lane 是运行期烤进 finalists.csv 的事实(_inject_pinned_finalists 强改判),比当前 pinned.jsonc
    # 更可靠(config 跑后可能被改)。genuine=真实精选(进 §3 buy-list);pinned=保送持仓(进 📌 表)。
    pinned_rows = [r for r in rows if str(r.get("lane", "")).strip() == "pinned"]
    genuine_rows = [r for r in rows if str(r.get("lane", "")).strip() != "pinned"]
    # 表头分母 + 命中队列 map(§3 与 📌 表共用;上移到两处渲染之前)
    n_l1 = meta.get("after_gate_a") or meta.get("universe") or len(l1_full) or "?"
    n_l2 = meta.get("l2_n") or len(l2_top) or "?"
    ch_map = {c: (r.get("recall_channels") or "") for c, r in l2_top.items()}   # 命中队列(随 keep 流到 L2 表)
    regime_line, regime_drift = regime_and_drift(scan_dir)

    out = [f"# A股扫描 v2 · Buy-List & 漏斗 — {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排(分层采样)→精排→研究→整合。L0/L1/L2 确定性,L3/L4 Claude 为引擎,"
           "**仅供研究,非投资建议。**_\n"]
    if regime_line:
        out.append(regime_line + "\n")

    # ── S1 情绪温度计一行(regime 行旁,presence-gated:temperature.csv 无当日读数 → 不加)──
    from autoresearch.scan.market import render_temperature_line  # lazy:避免 import cycle
    temp_line = render_temperature_line(analysis_date)
    if temp_line:
        out.append(temp_line + "\n")

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

    # ── 影子组合成绩单一行(spec 2026-07-05 wave §A1;presence-gated:文件缺 → 不加)──
    # 只在真实现场注入(与 run() 的 is_real 判据同姿势):tmp 测试目录从此不受开发机全局
    # reports/learning/paper_nav_summary.txt 污染(该文件由真实 prelude 跑动落盘,与 tmp scan_dir 无关)。
    if scan_dir == Path("context/scan") / analysis_date:
        pn = Path("reports/learning/paper_nav_summary.txt")
        if pn.exists():
            try:
                nav_line = pn.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                nav_line = ""
            if nav_line:
                out += [nav_line, ""]

    # (观察单日检节已退役 —— 用户裁定 fb_20260714_002:即便 watchlist_status.csv 在也不渲染。)

    # ── 📌 保送(pinned 直通;presence-gated:无 pinned.json/kept+expired 皆空 → 跳过)──
    pin_sec = _pinned_section(scan_dir, analysis_date, pinned_rows, l1_full, l2_top,
                              ch_map, vmap, n_l1, n_l2, pinned_path=pinned_path)
    if pin_sec:
        out += [pin_sec, ""]

    # ── 🎯 看多行业 top3(P7:确定性零 LLM;presence-gated,失败不挡发布)──
    try:
        from autoresearch.scan.market import market_pack as _mp2, render_sector_top3
        top3_sec = render_sector_top3(_mp2(scan_dir))
    except Exception:  # noqa: BLE001
        top3_sec = ""
    if top3_sec:
        out += [top3_sec, ""]

    sect = _sector_view_section(scan_dir)   # Phase 3:行业研判(briefs 研判段,方向性只在整合层)
    if sect:
        out += [sect, ""]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗(数量)"] + _funnel_rows(meta, len(keep) or "?", len(genuine_rows),
                                              len(rows), n_pinned=len(pinned_rows)) + [""]

    # ── 数据降级(presence-gated:无 degraded.json → 不加行)──
    # A 级(地基)违约会在取数处直接抛异常阻断,能出报告说明地基是全的;这里显示的是 B 级增强端点
    # 缺失(北向/质押/席位/公告…)。**降级必须可见** —— 否则读报告的人无从知道哪些旗是"真没有"、
    # 哪些是"没取到"(design 2026-07-12-data-contracts-design.md)。
    dline = _degraded_line(scan_dir)
    if dline:
        out += [dline, ""]

    # ── 2. 各阶段卡点 + 概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, f"确定性分层采样({meta.get('l2_engine', 'stratified')});sn_composite 排序+风格桶 floor+sector cap,零模型零 LLM。")
    from autoresearch.scan.menu import menu_health  # lazy:确定性菜单体检,缺 staging 自 ""
    mh = menu_health(scan_dir)
    if mh:
        out += ["", mh]
    out += ["", "**精排(L3)入选(风险/催化;论点见 buy-list 表 L3精排 列,不重复两遍)**:"]
    if finals:
        for fr in finals:
            out.append(f"- **{fr.get('name', '')}({fr.get('code', '')})** · {fr.get('sector', '')} — "
                       f"风险:{_strip(fr.get('risk', ''))};催化:{_strip(fr.get('catalyst', ''))}")
    else:
        out.append("_无 finalists.csv_")
    out.append("")

    # ── 3. 投资建议 ──(vmap 已在上方加载并折回评级;保送持仓已分列进「📌 保送持仓」节,这里只含真实精选)
    xref = ";保送持仓见「📌 保送持仓」节" if pinned_rows else ""   # 无保送时不留悬空引用
    out += [f"## 3. 投资建议(buy-list, {len(genuine_rows)} 只真实精选,按 评级 → 确信度 排序;"
            f"逐阶段结论{xref})\n"]
    out += _buylist_table_lines(genuine_rows, l1_full, l2_top, ch_map, vmap, n_l1, n_l2)
    out.append(f"\n_列注:**L1召回** #复合分名次/{n_l1}·命中队列(越小越强;低复合分票靠某条队列召回→名次很大);"
               f"**L2粗排** #分层重排名次/{n_l2}·gbdt分(遗留列名);**L3精排** = Opus holistic 论点 + conviction;"
               f"**L4研究·结论** = 决策卡深核后的关键定级依据(≥OW 取多头驱动,否则取空头/早停因);"
               f"置信度见各决策卡(30 行全『中』的列已删)。_")
    gh = _gate_histogram(scan_dir, genuine_rows)
    if gh:
        out += ["", gh]
    out += _verify_detail(vmap)
    from autoresearch.scan.calendar import calendar_section  # lazy:日历块,缺 staging 自 ""
    cal = calendar_section(scan_dir)
    if cal:
        out += ["", cal]
    out += ["", "### 组合视角", _portfolio_note(genuine_rows)]
    ens_lines = _ensemble_dissent_lines(emap)   # 🎭 spread≥2 人裁提示(presence-gated:无分歧 → [])
    if ens_lines:
        out += [""] + ens_lines
    chain = _same_chain_block(genuine_rows)  # Phase 3:同链 ≥2 卡并排(择链上最佳表达素材;保送不计)
    if chain:
        out += ["", chain]
    pos = _position_overlay(scan_dir, genuine_rows)
    if pos:
        out += ["", pos]
    out += [""]
    kn = _knowledge_note(rows)
    if kn:
        out += [kn]
    out += _stage_token_estimate(scan_dir)
    # ── ⏳ 待裁决提案 nag(presence-gated;仅真实现场注入,镜像 paper_nav 成绩单守卫防 tmp 测试污染)──
    if scan_dir == Path("context/scan") / analysis_date:
        nag = _proposals_nag()
        if nag:
            out += [nag, ""]
    out += ["## 诚实局限",
            "- 召回/粗排为启发式 + fwd_2_oc 超短主尺 IC 校准(L1 复合分、L2 sn_composite 同口径;T+1/T+5 参考),随 regime 漂移;L3/L4 为 Claude 推理产出。",
            "- 业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
            "- A股涨跌停/停牌使名义止损未必可执行(见各决策卡执行段)。",
            f"\n_明细 + 漏斗溯源:`reports/scan/{folder}/`(summary.md + details/〈名称〉.md + trace/;目录名=运行时刻,数据日见 manifest.json)_"]
    body = "\n".join(out)
    banner = _self_review_banner(scan_dir, rows, body, regime_drift=regime_drift)   # UZI self-review 硬门:fail 顶到最前
    return f"{banner}\n{body}" if banner else body


# ───────────────────────── 发布 ─────────────────────────


def _proposals_nag() -> str:
    """## ⏳ 待裁决提案(open 看板;presence-gated:缺文件/无 open/坏行 → "")。

    运营节奏 nag:proposals 攒着不裁 = 闭环学习卡死("过度建设跑动不足"的解药是节奏不是机制)。
    行渲染/排序/标注(龄·配对·疑失效)委托 feedback_store.proposals_nag_lines(看板自清洁,
    机器只整理不裁决);账本缺/空/坏行 → ""(原行为,parity 不破)。
    """
    try:
        from autoresearch.learning.feedback_store import proposals_nag_lines  # lazy 接线
        lines = proposals_nag_lines()
    except Exception:  # noqa: BLE001 — IO/渲染失败当无提案,不阻发布
        return ""
    if not lines:
        return ""
    return ("## ⏳ 待裁决提案\n" + "\n".join(lines)
            + "\n\n_提案满 20 交易日未裁将持续在此提醒;裁决走 feedback / scan-retro 流程,别攒。_")


def _safe_name(name: str) -> str:
    """股票名称 → 文件名安全(去 / \\ : * ? " < > | 与空白,*ST→ST);空则回退 未命名。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(name)).strip() or "未命名"


def _publish_details(scan_dir: Path, detail_out: Path) -> int:
    """把 L4 staging 决策卡发布到 details/,文件名用**股票名称**(非 ticker);只发当前 finalists。

    staging 卡仍以 <code>.md 暂存(parse_rating/retro 内部按 code);发布层改名 <名称>.md 便于人读。
    发布时若有 `_l4_intel_<code>.md`(活体情报盲搜稿)→ 原文附在卡片尾部(fb_20260714_004:
    读者要在 details 里直接看到当日新闻依据,不用去翻 staging)。附录只加在**发布副本**,
    staging 卡不动;parse_rating 两遍法先认卡面 `Rating:` 标签行,intel 中文文本不干扰评级解析。
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
        intel = scan_dir / f"_l4_intel_{code}.md"
        if intel.exists():
            try:
                body = intel.read_text(encoding="utf-8").strip()
            except OSError:
                body = ""
            if body:
                with dst.open("a", encoding="utf-8") as fh:
                    fh.write("\n\n---\n\n## 🕵️ 当日活体情报(盲搜原文·仅事实采集,评级不受此节影响)\n\n")
                    fh.write(body + "\n")
        # ── 价格断言对账(Wave1 ⑤-2,advisory;含 intel 附录一起对——pr_006 的捏造在 intel 侧)──
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            card_txt = dst.read_text(encoding="utf-8")
            res = price_claims.audit_card_text(
                card_txt, name=str(fr.get("name", "") or ""), code6=code,
                date=scan_dir.name, bars_fn=price_claims.bars_for)
            if res["n_claims"]:
                bad = res["mismatches"]
                if bad:
                    det = ";".join(f"{b['date'][4:6]}-{b['date'][6:]} 称"
                                   f"{('涨停' if b['kind'] == 'limit' else str(b['claimed']) + '%')}"
                                   f" 实为{b['actual']}%" for b in bad[:3])
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"**{len(bad)} 条不符** → {det}_\n")
                else:
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"0 条不符_\n")
                with dst.open("a", encoding="utf-8") as fh:
                    fh.write(line)
        n += 1
    return n


def _funnel_md(scan_dir: Path, analysis_date: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    n_pinned = sum(1 for r in finals if str(r.get("lane", "")).strip() == "pinned")   # 保送不占 L3 名额
    n_genuine = len(finals) - n_pinned
    lines = [f"# 漏斗溯源 — {analysis_date}\n", "六段:选集→召回→粗排(分层采样)→精排→研究→整合。\n"]
    lines += _funnel_rows(meta, len(keep) or "?", n_genuine, len(finals), n_pinned=n_pinned)
    lines += ["", f"权重来源:{meta.get('weights_source', '?')};L2 引擎:{meta.get('l2_engine', '?')};"
              f"universe 源:{meta.get('source', '?')}。",
              "各阶段明细见同目录 CSV(L1_recall_top1000 / L2_gbdt_top200 / L3_fine_finalists)。"]
    return "\n".join(lines)


def _archive_reasoning(scan_dir: Path, pdir: Path) -> int:
    """把各阶段 LLM 中间推理件(prompt/批表/keep-judged/calib)归档到
    trace/reasoning/{l2,l3,l4}/,让发布报告自带可追溯的 LLM 输入;缺失静默跳过。"""
    routes = [
        # L2 已下沉确定性(分层采样),无 LLM 推理件;L3 holistic 选股 + L4 级联 + Tier-3 验证留痕。
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
    sb = scan_dir / "sector_briefs"
    if sb.is_dir():                                     # Phase 3:行业 brief 随 trace 归档(留痕)
        dst = pdir / "sector_briefs"
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(sb.glob("*.md")):
            shutil.copy2(p, dst / p.name)
            n += 1
    (pdir / "funnel.md").write_text(_funnel_md(scan_dir, analysis_date), encoding="utf-8")
    n += _archive_reasoning(scan_dir, pdir)
    return n + 1


def run(analysis_date: str, scan_dir: Path | None = None, out_root: Path | None = None,
        hhmm: str | None = None, run_date: str | None = None,
        pinned_path: str | Path | None = None) -> Path:
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
    with contextlib.suppress(Exception):               # P0-4:逐卡过程分 checklist(presence-gated,失败不阻发布)
        from autoresearch.learning.process_score import write_process_scores
        write_process_scores(scan_dir)
    n_pipe = _publish_pipeline(scan_dir, out_base, analysis_date)   # trace/ 挂 out_base(details 同级)
    (out_base / "manifest.json").write_text(json.dumps(            # retro 按 analysis_date 定位本报告(目录名≠数据日)
        {"analysis_date": analysis_date, "generated_at": now.isoformat(timespec="seconds"), "hhmm": hhmm},
        ensure_ascii=False), encoding="utf-8")
    md = build_summary(scan_dir, analysis_date, hhmm, folder, pinned_path=pinned_path)
    summary_path = out_base / "summary.md"
    summary_path.write_text(md, encoding="utf-8")
    with contextlib.suppress(Exception):               # 现场导航页(第二天复盘入口)
        (out_base / "index.md").write_text(_health.index_md(scan_dir, out_base), encoding="utf-8")
    # 三个记账/刷新副作用共享同一条真实现场判据(resolve() 防相对/绝对路径假阴性)——
    # 测试 tmp 目录一律不触发,堵同类测试泄漏口(此前 sector_ledger 无门,曾单独裸奔)。
    is_real = Path(scan_dir).resolve() == (Path("context/scan") / analysis_date).resolve()
    if is_real:
        with contextlib.suppress(Exception):           # Phase 4:行业方向记账(sector_ledger,失败不阻发布)
            from autoresearch.learning.sector_ledger import record_calls
            n_calls = record_calls(scan_dir, analysis_date)
            if n_calls:
                print(f"[sector_ledger] 记 {n_calls} 条行业方向 → context/knowledge/sector_calls.jsonl")
        with contextlib.suppress(Exception):       # P7:top3 看多记账(分账,失败不阻发布)
            from autoresearch.learning.sector_ledger import record_top3
            from autoresearch.scan.market import market_pack as _mp3
            inds3 = [r["industry"] for r in (_mp3(scan_dir).get("sector_healthy_top3") or [])]
            n3 = record_top3(analysis_date, inds3)      # date 变量名与相邻 record_calls 调用一致,以现场为准
            if n3:
                print(f"[sector_ledger] 记 top3 看多 {n3} 条(source=deterministic_top3)")
        with contextlib.suppress(Exception):           # 影子买单记账(spec 2026-07-05 wave §A2,失败不阻发布)
            from autoresearch.learning.shadow_buys import record as _shadow_record
            n_sh = _shadow_record(scan_dir)
            if n_sh:
                print(f"[shadow_buys] 记 {n_sh} 只影子买单 → context/learning/shadow_buys.csv")
        with contextlib.suppress(Exception):           # 扫描日记刷新(失败不阻发布)
            from autoresearch.learning import journal as _journal
            _journal.main()
        with contextlib.suppress(Exception):           # P0-1(c):判例索引增量建库(precedents.build_index,失败不阻发布)
            from autoresearch.learning.precedents import build_index
            res = build_index()
            if res.get("dates_indexed"):
                print(f"[precedents] 索引 {len(res['dates_indexed'])} 新日 → context/knowledge/precedents.db")
        with contextlib.suppress(Exception):           # Wave3 ④:覆盖档案 δ 回写(§8+摘要机算;失败不阻发布)
            from autoresearch.dossier.delta import record_scan_deltas
            doss = record_scan_deltas(scan_dir, analysis_date)
            if doss["updated"]:
                print(f"[dossier] δ 回写 {doss['updated']} 份覆盖档案 → context/knowledge/dossiers/")
            for _c, _iss in sorted(doss["issues"].items()):   # I-4:lint 不吞(降级留痕)
                print(f"[dossier] ⚠️ 档案 lint:{_c} {_iss}")
            for _c, _sk in sorted(doss["sections_skipped"].items()):  # Wave3.5 I-2:镜像上一行
                print(f"[dossier] ℹ️ §4/§6 跳过刷新(素材缺,保留旧值):{_c} {_sk}")
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
