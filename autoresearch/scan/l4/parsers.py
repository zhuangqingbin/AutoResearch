"""L4 card, dashboard, gate, and early-stop parsers."""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.scan.report_model import EVIDENCE_MAX_CHARS

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

# ══ §6.7 评级同向「一句依据」(design: 2026-08-28-summary-slimdown-design.md §6.7)══════
#
# `_l4_brief` 的病:Hold/UW 卡只找「空」段,找不到就**回退到多空对撞首条空**,再找不到印 `—`
# —— 08-26 实测 10 张卡里 **3 张印 `—`**,而「为什么没买」恰是 0 买日那张表的全部信息量。
# 本函数是**并行入口**(不动 `_l4_brief` 与它的 96 字宽度 —— 旧调用方还在用),做两件事:
#   ① 空头链补齐到四级:L4 空侧 → `**早停**` 停因 → 失守门柱 → L3 risk;
#   ② **反向段绝不作为回退**(宁缺毋误导):给 Hold 卡印一句多头话术比印 `—` 更坏 ——
#      表里那一列是「与终评级同向的依据」,印反了就是报告在说假话。
#: `**早停**:停于 P2 ｜ 停因:资金流出` 的**原文**停因(不做 `_STOP_REASONS` 归一 ——
#: 归一会把自由文本一律吞成「其他」,而这里要的是给人读的那句话)。
_EVIDENCE_STOP_RE = re.compile(r"\*\*早停\*\*[:：][^\n]*?停因[:：]\s*([^\n｜|]+)")
#: 首句边界(§6.7):第一个 。/;/; 之前。
_EVIDENCE_SENT_END_RE = re.compile(r"[。;；]")
#: 极性 → 评级集合。**未知评级按空头处理**(与 `_l4_brief` 的 `rating in (Buy, Overweight)`
#: 同口径):拿不准时给空头证据是保守的,给多头证据是危险的。
_EVIDENCE_BULL_RATINGS = ("Buy", "Overweight")


def _evidence_clip(s: str, n: int = EVIDENCE_MAX_CHARS) -> str:
    """一句依据的统一收口:去 markdown / 管道符 / 换行 → 按**字符**截断加 `…`。

    全链唯一上限 `EVIDENCE_MAX_CHARS`(report_model 单一事实源),不再 80/96 两套。
    截断后总长恰为 `n`(n-1 个字符 + `…`)。
    """
    t = str(s or "")
    for mark in ("**", "__", "`"):
        t = t.replace(mark, "")
    t = t.replace("|", "/").replace("｜", "/")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^[\s\-•*#>]+", "", t).strip()
    t = t.strip(" =。;；:：、")
    return t if len(t) <= n else t[: n - 1] + "…"


def _evidence_first_sentence(s: str) -> str:
    """首句 = 第一个 `。`/`;`/`;` 之前(没有终止符 → 全文)。"""
    t = str(s or "").strip()
    m = _EVIDENCE_SENT_END_RE.search(t)
    return t[: m.start()] if m else t


def _bullbear_side(text: str, want: str) -> str:
    """L4「**一行多空**」里 `want`(多/空)那一侧;该侧缺席 → ""(**不取另一侧**)。"""
    m = _BULLBEAR_RE.search(text or "")
    if not m:
        return ""
    for seg in re.split(r"[｜|]", m.group(1)):
        s = seg.strip()
        if s.startswith(want):
            return s.lstrip("多空").strip(" :：")
    return ""


def _early_stop_reason(text: str) -> str:
    """`**早停**` 行的停因原文;退回自由文本 `早停因:`;都没有 → ""。"""
    m = _EVIDENCE_STOP_RE.search(text or "")
    if m:
        return m.group(1)
    m = _STOPWHY_RE.search(text or "")
    return m.group(1) if m else ""


def _gate_breach_text(text: str) -> str:
    """失守门柱 → `三门失守:主力真在、估值不透支`;无门柱段/无 ✗ → ""。

    走 `gate_status` 单一口径(它认识加粗 `**✗**` —— 17.4% 误判那道疤就在这里)。
    """
    st = gate_status(text or "")
    if not st:
        return ""
    bad = [g for g in _GATES3 if st.get(g)]
    return f"三门失守:{'、'.join(bad)}" if bad else ""


def pick_rating_aligned_evidence(card_text: str, final_rating: str,
                                 finalist_row: dict | None = None) -> dict:
    """与**终评级同向**的一句依据 → `{"text", "source", "polarity"}`(§6.7)。

    - 极性:`final_rating` ∈ {Buy, Overweight} → 只走多头链;其余(Hold/Underweight/Sell/
      未知/空)→ 只走空头链。**反向段绝不作为回退**。
    - 多头链:L4「一行多空」多侧 → L3 `thesis` 首句。
    - 空头链:L4「一行多空」空侧 → `**早停**` 停因 → 失守门柱 → L3 `risk` 首句。
    - 全空 → `{"text": "—", "source": "none", "polarity": "neutral"}`。
    - `source` ∈ `l4_bull|l4_bear|early_stop|gate|l3_thesis|l3_risk|none`(供 appendix C 留痕;
      summary 只印 `text`)。

    `finalist_row` = finalists.csv 的原行(读 `thesis`/`risk`);缺 → 只走卡内两/四级。
    """
    text = card_text or ""
    row = finalist_row if isinstance(finalist_row, dict) else {}
    if str(final_rating or "") in _EVIDENCE_BULL_RATINGS:
        polarity = "bull"
        chain: tuple[tuple[str, object], ...] = (
            ("l4_bull", lambda: _bullbear_side(text, "多")),
            ("l3_thesis", lambda: _evidence_first_sentence(row.get("thesis"))),
        )
    else:
        polarity = "bear"
        chain = (
            ("l4_bear", lambda: _bullbear_side(text, "空")),
            ("early_stop", lambda: _early_stop_reason(text)),
            ("gate", lambda: _gate_breach_text(text)),
            ("l3_risk", lambda: _evidence_first_sentence(row.get("risk"))),
        )
    for source, getter in chain:
        got = _evidence_clip(getter())
        if got and got != "—":
            return {"text": got, "source": source, "polarity": polarity}
    return {"text": "—", "source": "none", "polarity": "neutral"}


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
    # D8.3 ④:strict-with-warn —— 先认行首 `**Rating**:` 标签(契约干净);找不到才落回
    # 宽松兜底(行为读数不变,硬切是 P2 D8.2 的事),但打印一行 stderr 留痕:少了这行标签
    # 不该是静默发生的事,即便读数最终碰巧一样。
    rating = parse_rating(text, strict=True)
    if rating is None:
        rating = parse_rating(text)
        code = fr.get("code") or ticker
        print(f"[card-contract] {code} Rating 行缺失,已启用全文兜底", file=sys.stderr)
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

# Wave10 A11 复核实测(2026-08-01):`**` 强调号是**卡片里的主流写法**,而此前的跳过逻辑
# 只跳空白。全语料 241 张带门柱段的卡里 **42 张(17.4%)** 因此被判成「三门全过」——
# 方向还是单向的:**真失守被读成通过**。链路上的每一环都吃了这个错:
#   decision_records 记 PASS → `decision_gate_bucket` 返回 None → 该票**根本不进门账本**;
#   门柱直方图少数;影子买单的 binding 列空;首因分类从 `L4_GATE_*` 掉到
#   `L4_RUBRIC_SCORE`/`DATA_UNDECIDABLE`;A11 的门归因 v3 全盘继承。
# 症状很像「这道门最近没怎么拦人」,而事实是「解析器看不懂加粗」。
_EMPHASIS = "*_`"
#: D8.3 ①:门记号变体字形容错 —— `✔`(粗体对勾常见写法)、`✘`/`×`(叉号常见写法)与
#: 规范 `✓`/`✗` 同解。归一化在 `_mark_after` 内做,`_parse_gate_seg` 等下游只认规范两态。
_GLYPH_NORMALIZE = {"✔": "✓", "✘": "✗", "×": "✗"}


def _mark_after(seg: str, gate: str) -> str:
    """门名之后的 ✓/✗ 标记(容错:「门」后缀、空白、markdown 强调号、✔/✘/× 变体字形);
    没有 → ""。"""
    i = seg.find(gate)
    if i < 0:
        return ""
    j = i + len(gate)
    if seg[j:j + 1] == "门":                    # 措辞容错:「主力真在门✗」
        j += 1
    while seg[j:j + 1] and (seg[j].isspace() or seg[j] in _EMPHASIS):
        j += 1                                  # 空白 + `**`/`__`/`` ` `` 一并跳过
    ch = _GLYPH_NORMALIZE.get(seg[j:j + 1], seg[j:j + 1])
    return ch if ch in ("✓", "✗") else ""


def _parse_gate_seg(seg: str) -> dict[str, bool]:
    """单段『OW三门…』文本 → {门: 是否✗};找不到标记判 False(gate_status 的既有语义)。"""
    return {g: _mark_after(seg, g) == "✗" for g in _GATES3 if g in seg}

def _seg_has_mark(seg: str) -> bool:
    """段内是否至少一个门名带着实际 ✓/✗ 标记——用来判该段是否「可解析」。"""
    return any(_mark_after(seg, g) for g in _GATES3)

def gate_status(text: str) -> dict[str, bool] | None:
    """解析卡文『OW三门…』段 → {门: 是否✗失守};无门柱段(如早停卡)、或段内一个 ✓/✗
    记号都没有(纯散文提及,没有结构化判定)→ None。门柱直方图统一走本函数(单一口径,
    防漂移)。

    容错(漏斗 P0+P1 波 Task 2b 修复):①门名与 ✓/✗ 之间允许空白(l4-card.md 满卡模板 Rubric 行的
    真实写法「主力真在 ✗」带空格);②卡片正文可能多处出现"OW三门"字样(如先散文一句带过、文末
    Rubric 行才结构化判定)——取全部匹配段里**最后一个**能解析出至少一个 ✓/✗ 标记的段;若全部段
    都解析不出标记 → None(D8.3 ①,2026-08-31 修口径)。

    ⚠️ 这条 None 分支此前是"退回首段"、把"一个字都没判"读成"三门全过"(`_mark_after`
    对没写标记的门名返回 "",`"" == "✗"` 恒 False)——与「读不懂加粗 `**✗**` → 17.4%
    的卡被判三门全过」同族的"解析器猜不出就悄悄放行"病,只是触发条件从"看不懂加粗"
    换成"压根没写"。下游消费者(`decision_finalize._build_decision_records` 的
    `gate_states` 只在 `gates is not None` 时才更新、`l4/parsers._gate_breach_text`
    与 `report_sections.gate_histogram` 都已经把 `None`/falsy 当"跳过,不计入分母"处理)
    ——None 本就是这些消费者认识的合法输入,早停卡从来就在传它。"""
    matches = list(_GATESEG_RE.finditer(text))
    if not matches:
        return None
    for m in reversed(matches):
        seg = m.group(0)
        if _seg_has_mark(seg):
            return _parse_gate_seg(seg)
    return None

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
