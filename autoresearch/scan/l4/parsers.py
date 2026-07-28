"""L4 card, dashboard, gate, and early-stop parsers."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import parse_rating

_PROPOSAL_RE = re.compile(
    r"FINAL TRANSACTION PROPOSAL[:\s*]*\**\s*(BUY|HOLD|SELL)",
    re.IGNORECASE,
)
_CONF_RE = re.compile(r"置信度[:：]\s*\**\s*([高中低]+)")
_RUBRIC_RE = re.compile(
    r"Rubric[^\n]*?(Buy|Overweight|Hold|Underweight|Sell)",
    re.IGNORECASE,
)
_DEV_RE = re.compile(r"\*\*\s*偏离\s*\*\*")
_BULLBEAR_RE = re.compile(r"\*\*一行多空\*\*[:：]?\s*(.+)")
_STOPWHY_RE = re.compile(r"早停因[:：]\s*(.+?)(?:→|\*\*|$)")
_BEARBULLET_RE = re.compile(r"[-•]\s*空[:：]\s*(.+)")
_GATES3 = ("主力真在", "业绩真兑现", "估值不透支")
_GATESEG_RE = re.compile(r"OW三门[^\n→]*")
_EARLYSTOP_RE = re.compile(
    r"\*\*早停\*\*[:：]\s*停于\s*(P[0-9])\s*[｜|]\s*停因[:：]\s*([^\s｜|]+)"
)
_STOP_REASONS = (
    "数据不足", "涨停追高", "题材透支", "资金流出",
    "估值透支", "基本面恶化", "其他",
)


def parse_ratings_from_details(details_dir: Path | str) -> dict[str, str]:
    """读 details/*.md 决策卡,复用项目 `parse_rating` 提五档评级 → {code: rating}。

    code = 文件名 stem(6 位代码);读不到卡/无评级 → `parse_rating` 回退 'Hold'。
    """
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    out: dict[str, str] = {}
    base = Path(details_dir)
    if not base.exists():
        return out
    for p in sorted(base.glob("*.md")):
        code = p.stem
        out[code.zfill(6) if code.isdigit() else code] = parse_rating(p.read_text(encoding="utf-8"))
    return out

def pick_opportunity_candidates(ratings: dict[str, str], scan_dir, k: int = 2) -> list[str]:
    """**机会成本红队名单**(0买日;spec 2026-07-02 任务E):rubric 分最高的 Hold top-k。

    对称性修复:买单有 skeptic 红队,空仓从来没有——连续 0 买后系统无法自证"门太紧还是
    市场真没货"。每只派一个独立 Opus **bull 方**立论、PM 三透镜裁判;产出**只进观察单
    (结构化 conds)与校准数据,不改评级**(门的松紧不动)。排序键 = finalists.csv 的
    L3 conviction(确定性、现成);缺 finalists → []。
    """
    from pathlib import Path

    import pandas as pd
    f = Path(scan_dir) / "finalists.csv"
    holds = {str(c).zfill(6) for c, r in ratings.items() if r == "Hold"}
    if not holds or not f.exists():
        return []
    df = pd.read_csv(f, dtype={"code": str})
    if "code" not in df.columns:
        return []
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["_cv"] = pd.to_numeric(df.get("conviction"), errors="coerce").fillna(0)
    df = df[df["code"].isin(holds)].sort_values("_cv", ascending=False, kind="stable")
    return df["code"].head(k).tolist()

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
