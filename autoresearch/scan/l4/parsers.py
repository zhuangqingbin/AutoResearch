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


def read_card_text(scan_dir: Path, ticker: str) -> str | None:
    """定位 finalist 的 lite 决策卡:context/scan/<date>/details/<ticker>.md,按 6 位代码 glob 兜底。

    公共入口(Task 6,2026-09-12 scene-reconstruction 设计 §3);原私有名 `_decision_text`
    仍保留为别名(见下),`decision_finalize`/`report_sections`/`l4.card_io`/`assemble`
    的既有 import 不必改动。
    """
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


#: 向后兼容别名(原私有名)——既有调用方按这个名字导入,行为与 `read_card_text` 完全相同
#: (同一个函数对象,不是重复实现)。
_decision_text = read_card_text

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

# ══ Task 6(2026-09-12 scene-reconstruction 设计 §7.1):card_context 保守解析 ═══════
#
# E6(`relative_buy.py`)长期只读卡的机读提案(`_PROPOSAL_RE`),不读卡面仓位/触发位/
# 执行线——于是写着"不新开仓"的卡也能被相对层选成当日 BUY(设计稿附录 A E12:四笔
# 亏损 BUY 现场,四张卡全写不建仓/早停不建仓/不追)。本节新增两个公共入口把这些信号
# **保守地**解析出来,交给 Task 7(`relative_buy.py`)接进 E6 解释文档——本节自己
# 不改候选池/硬门/排序/评级规则,也不碰 relative_buy.py。
#
# 设计边界(供 reviewer 核对):`parse_card_context` 只吃卡面原文(+ 可选的历史 contract
# 阈值),不知道文件路径/hash/是否事后补录——spec §7.1 的 `source` 字段需要那些身份
# 信息,由 Task 7 在本函数返回值之上补一层(它握着 scan_dir/code/capsule 引用),不在
# 这里生产。
#
# 四态判定只认**正证据**(task-6-brief 控制者裁决 + spec §7.1 段落):
#   PROHIBITED  — 仓位为完整零值(0%/0.0%,允许尾随注释如"0%(不新建仓)")
#                 或命中 不建仓/不新开仓/不新建仓(spec 给定的封闭三词,不外推释义变体)。
#   CONDITIONAL — 命中 待突破确认/满足条件才考虑/不追高(同样是封闭三词)。
#   ALLOWED     — 命中"允许/建议/可/可以"紧跟"新开仓/新建仓"且前一字不是否定字——
#                 绝不能从"没找到否定词"反推允许;裸数字(如"10%")不是推荐,不算证据。
#   UNKNOWN     — 其余(含词表外的释义变体,如"不可以新开仓"——不在封闭词表内,不外推)。
# 证据来自 `position_raw`(仓位列)∪ `trigger_raw`(触发位列):早停卡没有仓位列,
# "不新开仓"这类判词写在触发位里,两栏都要看;零值判定单独锚定在 `position_raw`
# 开头(`^0(?:\.0+)?%`),前缀锚点保证"10%"不会被读成"0%"的子串误命中。
# 否定/零仓位证据与允许证据同时出现时,保守记 PROHIBITED,冲突记进 `parse_errors`
# ——不是新开一个"conflicts"键,spec 那个键属于 Task 7 的 E6 解释层产物。
_STANCE_ZERO_RE = re.compile(r"^0(?:\.0+)?\s*%")
_STANCE_PROHIBIT_WORDS = ("不建仓", "不新开仓", "不新建仓")
_STANCE_CONDITIONAL_WORDS = ("待突破确认", "满足条件才考虑", "不追高")
_STANCE_ALLOW_RE = re.compile(r"(?<![不无未非禁勿])(?:明确)?(?:允许|建议|可以?)新(?:开仓|建仓)")
#: fix round 3(2026-09-25):原 `.search` + 无锚正则会命中**任意位置**的字面串——包括
#: agent 定义里教它怎么写这一行的规则原文本身(`允许 | 禁止 | 条件(...)`,三选一并列
#: 用管道分隔),以及散文里提及"入场"两字附近偶然凑出的子串。若 agent 把指令原文
#: 抄进卡面正文,旧正则会把规则原文的「允许」读成真实结论——即使卡面后面另有一条
#: 真实的 `**入场**: 禁止`,`.search` 只取**第一个**匹配,真结论被规则原文抢先顶替。
#: 两处收紧对应两种写法:①`(?m)^[ \t]*` 锚到行首(只容许前导空白)——真实入场行
#: 独占一行,规则原文通常嵌在散文/反引号里,不在行首;②`(?!\s*[|｜])` 拒绝紧跟
#: 管道符的候选——规则原文把三个选项用 `|`/`｜` 并列,真实入场行只会二选一/三选一
#: 地**写一个值**,后面不会跟着"另一个选项"。两条防线独立生效,任一条命中就能挡下
#: 对应的误读写法。
_ENTRY_LINE_RE = re.compile(r"(?m)^[ \t]*\*\*入场\*\*[:：]\s*(允许|禁止|条件)(?!\s*[|｜])")
_ENTRY_LINE_STANCE = {"允许": "ALLOWED", "禁止": "PROHIBITED", "条件": "CONDITIONAL"}
#: 多条入场行分歧时的保守取值序——**绝不取 ALLOWED**(见 `_resolve_entry_line_matches`)。
_ENTRY_STANCE_CONSERVATIVE_ORDER = ("PROHIBITED", "CONDITIONAL", "UNKNOWN")


def _resolve_entry_line_matches(values: list[str]) -> tuple[str, bool]:
    """`_ENTRY_LINE_RE.findall` 命中的全部机读入场行原文 → `(entry_stance, conflict)`。

    fail-closed(fix round 3):一张卡即使收紧了正则,仍可能出现**不止一条**匹配
    (例如规则原文虽被行首锚点挡掉,但卡面自己重复写了两条不一致的入场行)。这个
    字段能一票否决交易——多条一致就照旧取值;多条**分歧**时绝不能取 ALLOWED,
    按 PROHIBITED > CONDITIONAL > UNKNOWN 的保守序,取分歧集合里能取到的最保守项
    ——即使分歧里含 ALLOWED,也只在其余候选都不比它更保守时才轮到它(而三值排他,
    这种情况不会发生:分歧至少两个不同值,较保守的那个必然是 PROHIBITED 或
    CONDITIONAL 之一)。`conflict=True` 时调用方须在 `parse_errors` 留痕。
    """
    stances = {_ENTRY_LINE_STANCE[v] for v in values}
    if len(stances) == 1:
        return next(iter(stances)), False
    for candidate in _ENTRY_STANCE_CONSERVATIVE_ORDER:
        if candidate in stances:
            return candidate, True
    return "UNKNOWN", True  # 只有三个可能取值,理论不可达;防御性兜底同样不取 ALLOWED

#: `[执行线]` 两个已知字段名(与 `contracts.agent_output.L4_CARD` 的
#: `exec_line_pct`/`exec_line_pos` 同源)。本模块**不 import**
#: `EXEC_LINE_MAX_PCT_1D`/`EXEC_LINE_MAX_POS_IN_RANGE`——只拿这两个字符串常量名去
#: `contract` 参数里查,历史阈值必须由调用方(留存当时版本的一方)显式传入,绝不能让
#: "没传 contract" 退化成"拿今天的常量去判历史卡漂移"(task-6-brief 第4条硬约束)。
_EXEC_METRICS = ("pct_chg", "pos_in_range")
_EXEC_CONTRACT_KEYS = {
    "pct_chg": "EXEC_LINE_MAX_PCT_1D", "pos_in_range": "EXEC_LINE_MAX_POS_IN_RANGE",
}
_EXEC_PRESENCE_RE = {
    metric: re.compile(r"\[执行线\][^\n]*\b" + re.escape(metric) + r"\b")
    for metric in _EXEC_METRICS
}
_EXEC_VALUE_RE = {
    metric: re.compile(
        r"\[执行线\]\s*" + re.escape(metric) + r"\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)")
    for metric in _EXEC_METRICS
}


def _entry_stance(position_raw: str | None, trigger_raw: str | None) -> tuple[str, bool]:
    """新开仓四态(仅凭正证据)。返回 `(entry_stance, conflict)`;
    `conflict=True` 表示否定/零仓位证据与允许证据同时出现(仍保守记 PROHIBITED)。
    """
    pos = position_raw or ""
    combined = f"{pos} {trigger_raw or ''}"
    is_zero = bool(_STANCE_ZERO_RE.match(pos.strip()))
    has_prohibit = is_zero or any(w in combined for w in _STANCE_PROHIBIT_WORDS)
    has_conditional = any(w in combined for w in _STANCE_CONDITIONAL_WORDS)
    has_allow = bool(_STANCE_ALLOW_RE.search(combined))
    if has_prohibit:
        return "PROHIBITED", has_allow
    if has_conditional:
        return "CONDITIONAL", False
    if has_allow:
        return "ALLOWED", False
    return "UNKNOWN", False


def _no_new_position(stance: str) -> bool | None:
    """兼容字段(spec §7.1):PROHIBITED→True;ALLOWED→False;CONDITIONAL/UNKNOWN→None。"""
    if stance == "PROHIBITED":
        return True
    if stance == "ALLOWED":
        return False
    return None


def _exec_line_raw(text: str, metric: str) -> str | None:
    """卡面里提到该 `[执行线]` 字段的那一整行原文(去项目符号,`_strip` 去强调号);
    没写就 None——presence 只问"这行是否存在",不依赖数字能否解析。"""
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-•*").strip()
        if _EXEC_PRESENCE_RE[metric].search(line):
            return _strip(line)
    return None


def _exec_value(raw: str, metric: str) -> tuple[str | None, float | None]:
    """从已定位的执行线原文里取 `(操作符, 阈值)`;数字损坏/缺失 → `(None, None)`
    (正则的捕获组只在数字形状完整时才命中,`float()` 不会抛)。"""
    m = _EXEC_VALUE_RE[metric].search(raw)
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


def _parse_exec_lines(text: str, contract: dict | None,
                      parse_errors: list[str]) -> dict[str, dict]:
    """`exec_lines` 两个字段:presence 与 contract_match 分开计算、互不派生
    (spec §7.1 硬约束)。`contract_match` 是三态字符串
    (`MATCH`/`DRIFTED`/`UNKNOWN`)——与 `agent_output.GATE_STATES` 同一防漂移理由:
    bool 表不了"未知",不进 JSON。
    """
    contract_version = contract.get("version") if isinstance(contract, dict) else None
    out: dict[str, dict] = {}
    for metric in _EXEC_METRICS:
        raw = _exec_line_raw(text, metric)
        presence = raw is not None
        op = threshold = None
        if presence:
            op, threshold = _exec_value(raw, metric)
            if threshold is None:
                parse_errors.append(f"exec_lines.{metric}: 阈值数字无法解析,已保留原文")
        contract_match = "UNKNOWN"
        if presence and threshold is not None and isinstance(contract, dict):
            want = contract.get(_EXEC_CONTRACT_KEYS[metric])
            if isinstance(want, (int, float)) and not isinstance(want, bool):
                contract_match = "MATCH" if abs(float(want) - threshold) < 1e-9 else "DRIFTED"
        out[metric] = {
            "raw": raw, "op": op, "threshold": threshold,
            "presence": presence, "contract_match": contract_match,
            "contract_version": contract_version,
        }
    return out


def _empty_exec_lines(contract: dict | None) -> dict[str, dict]:
    contract_version = contract.get("version") if isinstance(contract, dict) else None
    return {metric: {"raw": None, "op": None, "threshold": None, "presence": False,
                     "contract_match": "UNKNOWN", "contract_version": contract_version}
            for metric in _EXEC_METRICS}


def _empty_card_context(card_kind: str, parse_errors: list[str],
                        contract: dict | None) -> dict:
    """三处早退路径(空文本/无仪表盘/外层 try 兜底)共用的空壳。`entry_source` 恒
    `None`(fix round 1)——这里的 `None` 不是"没找到入场行",是"压根没能走到去找入场
    行/散文的那一步":哪怕卡面其实写了 `**入场**` 行,只要仪表盘解不出来,
    `_parse_card_context_impl` 在够到 `_ENTRY_LINE_RE.findall` 之前就已经从这里退出
    了。`entry_source` 记的是"问过哪个机制",不是"问出了什么答案"——三值完整语义见
    `parse_card_context` 的 docstring。
    """
    return {
        "card_kind": card_kind, "proposal": None, "ev_target": None, "rr": None,
        "position_raw": None, "trigger_raw": None,
        "entry_stance": "UNKNOWN", "entry_source": None, "no_new_position": None,
        "exec_lines": _empty_exec_lines(contract),
        "parse_status": "ERROR", "parse_errors": parse_errors,
    }


def _parse_card_context_impl(text: str | None, contract: dict | None) -> dict:
    parse_errors: list[str] = []
    body = text if isinstance(text, str) else ""
    if not body.strip():
        parse_errors.append("text: 卡片正文为空或缺失")
        return _empty_card_context("unknown", parse_errors, contract)

    dash = _parse_dashboard(body)
    if not dash:
        parse_errors.append("dashboard: 未找到可解析的『评级』表(空表或格式不可辨认)")
        return _empty_card_context("unknown", parse_errors, contract)

    if parse_early_stop(body) is not None:
        card_kind = "earlystop"
    elif "仓位" in dash:
        card_kind = "full"
    else:
        card_kind = "unknown"
        parse_errors.append("card_kind: 表存在但既无早停行也无仓位列,判定为未知卡种")

    prop_m = _PROPOSAL_RE.search(body)
    proposal = prop_m.group(1).upper() if prop_m else None
    if proposal is None:
        parse_errors.append("proposal: 未找到 FINAL TRANSACTION PROPOSAL 行")

    ev_target = _get(dash, "EV目标", "目标") or None
    rr = _get(dash, "R:R") or None
    position_raw = _get(dash, "仓位") or None
    trigger_raw = _get(dash, "触发位") or None

    # entry_source 三值(fix round 1;完整定义见 parse_card_context docstring):
    # "line"  命中 **入场** 行,直接采用;
    # "prose" 没有该行,退回仓位/触发位散文推断——不论推断结果是不是 UNKNOWN,只要
    #         散文推断真的跑过就是 "prose",不能因为答案含糊就悄悄记成 None。
    # (第三值 None 不在这个分支产生,见 _empty_card_context 的早退路径。)
    line_matches = _ENTRY_LINE_RE.findall(body)   # findall,非 search:见 fail-closed 理由
    if line_matches:                              # 机读入场行优先(2026-09-24 §2.5)
        stance, line_conflict = _resolve_entry_line_matches(line_matches)
        conflict, entry_source = False, "line"
        if line_conflict:
            parse_errors.append(
                "entry_stance: 卡片含多条互相矛盾的机读入场行,已按保守序取值,不取 ALLOWED")
    else:
        stance, conflict = _entry_stance(position_raw, trigger_raw)
        entry_source = "prose"
    if conflict:
        parse_errors.append(
            "entry_stance: 否定/零仓位证据与允许新开仓证据同时出现,保守记 PROHIBITED")

    return {
        "card_kind": card_kind,
        "proposal": proposal, "ev_target": ev_target, "rr": rr,
        "position_raw": position_raw, "trigger_raw": trigger_raw,
        "entry_stance": stance, "entry_source": entry_source, "no_new_position": _no_new_position(stance),
        "exec_lines": _parse_exec_lines(body, contract, parse_errors),
        "parse_status": "PARTIAL" if parse_errors else "OK",
        "parse_errors": parse_errors,
    }


def parse_card_context(text: str, *, contract: dict | None = None) -> dict:
    """决策卡文本 → card_context(spec 2026-09-12 scene-reconstruction 设计 §7.1)。

    保守解析:缺失记 `None`,格式错误记 `parse_errors`,**任何异常都不向上抛出**
    ——E6 不能因为一张形状意外的卡丢掉整份决策文档(task-6-brief 条款8)。字段级
    解析(`_parse_card_context_impl`)已经对每一步做了防御性处理(正则不命中就是
    `None`,从不对未经校验的文本调用 `float()`);下面这层 `try/except` 是兜底,
    不是主路径——真出现未预期异常时仍要留下原因,不能静默退化成"看起来正常的空
    结果"(设计 §2 全局约束:不能用宽泛 try/except 吞掉错误又不留痕)。

    `contract`:当时留存的执行线阈值,形如
    `{"EXEC_LINE_MAX_PCT_1D": 3.0, "EXEC_LINE_MAX_POS_IN_RANGE": 0.7, "version": "..."}`。
    不传(`None`,默认)时 `exec_lines.*.contract_match` 恒为 `"UNKNOWN"`——本函数不读
    `contracts.agent_output` 的当前常量,不会拿今天的阈值顶替历史版本去判"漂移"。

    不产出 `source`(相对路径/内容 hash/版本/是否事后补充):那些字段需要调用方已知
    的文件身份与归档状态,本函数只接收卡面原文,由 Task 7 组装
    `_relative_buy_decision.json` 时在这份返回值之上补上。

    `entry_source`(Task 17,fix round 1)记录的是**这次解析问过哪个机制,不是那个
    机制给出的答案好不好**。三值:

    - `"line"`  卡面存在可机读的 `**入场**` 行,直接采用其结论;
    - `"prose"` 没有该行,退回仓位/触发位散文推断;**不论推断结果是 ALLOWED /
      PROHIBITED / CONDITIONAL 还是 UNKNOWN,只要散文推断真的跑过就是
      `"prose"`**——`entry_stance="UNKNOWN"` 配 `entry_source="prose"` 是合法组合,
      不是待修的 bug,不能"纠正"成 `None`;
    - `None`    没有做任何抽取尝试——**不止"没写入场行"这一种情况**:卡面即使真的
      写了入场行,只要仪表盘本身解不出来(`_parse_dashboard` 早退,或外层 `try`
      兜底真异常),也轮不到检查有没有入场行,同样记 `None`(见
      `_empty_card_context` 的三处调用点)。下游据此可以分清"agent 压根没被问过
      入场问题"(契约缺口)与"agent 给出了明确判断,只是散文没写清楚"(研究判断)——
      前者才是 `None`,后者哪怕含糊也是 `"prose"`。
    """
    try:
        return _parse_card_context_impl(text, contract)
    except Exception as exc:  # 本函数唯一允许的全局兜底——见上方 docstring,留原因不吞错。
        return _empty_card_context(
            "unknown",
            [f"parse_card_context: 未预期异常 {type(exc).__name__}: {exc}"],
            contract,
        )
