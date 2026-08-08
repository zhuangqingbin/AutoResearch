#!/usr/bin/env python3
"""统一相对决策层 finalizer v1(Wave12 E6-1)—— scan 路线**唯一**拥有最终 BUY 的组件。

design: Wave12 E6-1(用户 2026-08-08 六条裁定)。产物
`context/scan/<date>/_relative_buy_decision.json`。纯确定性:**零 LLM、零联网、只读结构化
产物**。

## 病灶

07-15→08-06 连续 17 个扫描日 0 买单(178 张决策卡里 Overweight = 0)。根因不是"门太严",
而是**旧系统只回答「有没有绝对强到值得买」,却被要求同时回答「今天全市场相对最值得买
谁」**——拿一套绝对门处理两个问题,必然长期 0 BUY。本模块把第二个问题单独立层:

- 对外**只有一个** `BUY` 信号(不设"质量 BUY / 游资 BUY"多套);
- 每个**成功完成**的交易日至少给出一只;
- 最低那一只是**相对 BUY**——"在今日可交易全集里相对最值得买",**不承诺绝对上涨**;
- 相对基准 = 全市场可交易等权为主(`rel_gap_market`)、行业中性超额为辅(`rel_gap_sector`),
  两列都是主尺 `MAIN_RULER`(gap_c1_o2)的超额,不是第二把尺。

## 本轮是影子(shadow)

只写自己那一份 JSON:**不改任何生产发布行为、不写 buy ledger、不碰 publisher、不动
`decision_records.json`、不改任何 prompt**。`mode` 只接受 `"shadow"`;活体切换是独立的
GATED task(≥20 个真实扫描日影子 + 五守卫 + 人工批准),不在这里开后门——"默认不启用"
必须连副作用一起不启用。

## v1 规则(**观察前锁定**;任何改动 = 新 `RULE_VERSION` + experiment_registry)

边看结果边调参数 = 作弊。下面每条都是在看到任何一天的影子输出**之前**写死的。

**候选集** = 当日 L4 派发过的票(护照 `l4.dispatched`)。L2 菜单里没走到 L4 的票不是
BUY 候选,也不进 `excluded`——把 190 只 pass1 被切的票记成"被排除"是噪音,不是信息
(护照 `missing[]` 同款教训)。

**硬资格四类**(全过才 `eligible`):

1. `tradable` —— 在当日 **L0 可交易全集**内(`L1_scored_full.csv` 的行)∧ 非 ST/退 ∧ 入场旗
   为真。入场旗列名走 `ruler.entry_flag_for()` **单点**取(严禁写死 `"buyable"` 字面量,
   见 2026-08-08 final-review C1:换尺后 10 个消费点全在读旧腿)。
   ⚠️ **premise 偏差(实测)**:`ruler.entry_tradable` 的旗 `buyable_c1` 由
   `factor_lab.forward_returns` 从 **T+1 收盘**算出——决策当晚它在活体产物里根本不存在
   (实测 `L1_scored_full.csv` 无该列)。ruler 自己的语义是"列整体不存在 → 全 `default`
   (True)",所以活体日这一项等价于"在 L0 可交易全集内";回放帧带上该列时它自动生效。
   这不是替代判据,是同一函数在两种输入下的既定行为。
2. `data_a` —— 当日 A 级数据契约无未解决异常。**日级**判定(全体候选同值),结构化读
   `run_health.json`:`core_missing` 空 ∧ `run_contract.status == "OK"` ∧
   `stage_results.status != "INVALID"` ∧ `stage_results.failed` 空 ∧
   `decision_records.status ∉ {"INVALID","MISMATCH"}`。`run_health.json` 缺失 → 判 **False**
   (A 级契约无从判定时按阻断处理,与 `contracts.py` "A 级空即抛异常阻断"同向)。
3. `contract` —— 该票 slim/卡/价格断言契约完整:task-book(`_l4_tasks.json`)该票
   `status == "SUCCEEDED"` ∧ `artifacts.slim/card` 均 `PRESENT` ∧ 价格断言无 fail。
   ⚠️ **premise 偏差(实测)**:**逐票**价格断言状态在 `context/scan/<date>/` 里没有任何
   生产者——`price_claim_subjects.json` 是日级聚合(无 code),发布层只往
   `reports/scan/<run_id>/details/<中文名>.md` 追一行文字。故本模块读一份 presence-gated 的
   可选产物 `_price_claim_status.json`(`{code: {"n_claims", "n_mismatch"}}`);缺席 →
   `UNMEASURED`,**不当 fail 用**(缺证据不等于有罪),并在 `inputs` 留痕。
4. `no_redflag` v1 判定 = 非 ST/退 ∧ `research_rating != "Sell"` ∧ 早停原因 ∉
   `REDFLAG_EARLY_STOP_REASONS` ∧ 当日成交额分位 ≥ P10(**L0 可交易全集内**)。
   ⚠️ **premise 偏差(实测)**:早停停因是七选一的机读词表(`l4/parsers.py`:数据不足/
   涨停追高/题材透支/资金流出/估值透支/基本面恶化/其他),里面**没有**"监管/审计红灯"
   这一档。规则逐字实现(该 token 留在集合里),但它在现行词表下**永不命中**——见文件尾
   「v1 已知问题」。

**四个面**(各自转**当日候选内**的中位分位,等权 Borda 平均):

- `target_align` = composite 分当日百分位(护照 `recall.composite_pctl`);
- `recall_strength` = 0.5×pctl(`n_channels`) + 0.5×max(逐路分位);
- `evidence` = 满卡 1.0 / 早停 0.4 基础分 + intel 在场 +0.2 + 档案在场 +0.2 +
  price_claim 干净 +0.2,截到 [0,1];
- `risk_safety` = 1 − 归一风险分(risk_flags 计数 + 早停风险类 + tripwire 严重度)。

**「面内缺失」的 v1 口径**(规则原文只说"面内缺失 = 该面 0.5 并记 `missing`",这里把
"缺失"钉死,免得两个人读出两种意思):该面的**全部**输入都取不到 → 面 = 0.5 并记
`faces_missing`,且该票**不进这一面的分位分母**(否则一个盲票会把别人的分位一起挪走);
只缺部分分量 → 用可得分量,权重在可得分量间重新归一。

**出单**:`eligible` 最高分 = 第 1 只 `BUY(basis=relative)`。并列决胜:`target_align` →
流动性(当日成交额)→ 代码字典序。

**第 2 只起**:`relative_decision_score` 达已验证阈值 ∧ 同 bucket×regime 的 OOS 历史绝对
gap 扣成本为正 —— **v1 影子期无已验证阈值(`SECOND_BUY_THRESHOLD is None`),该门恒不
满足,第 2 只恒不出**。这是设计不是 bug,已被测试钉死。

**`expected_abs_gap`**:同 score bucket×regime 的 OOS 历史均值/CI;样本 <
`EXPECTED_ABS_GAP_MIN_N`(20)→ `UNMEASURED`。**v1 影子期恒 `UNMEASURED`,禁止拍数**,
同样被测试钉死。

**BLOCKED**:全部候选被硬资格否决(或当日无候选)→ `blocked=true` + 分桶
`blocked_reasons`。影子期**只记录**,不影响生产发布。

## 确定性

同输入重复构建 byte 稳定:不写时间戳、不写随机序,所有由数据决定的集合显式排序,落盘走
`sort_keys=True`,浮点一律 `round(…, 6)`。A 股代码一律 stdlib csv 读字符串 + `zfill(6)`
(pandas 会把 `001283` 读成 `1283`)。

  uv run --no-sync python -m autoresearch.scan.relative_buy <date>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

from autoresearch.common.ruler import MAIN_RULER, REL_MARKET, REL_SECTOR, entry_flag_for
from autoresearch.scan.passport import build_passport

SCHEMA_VERSION = 1
RULE_VERSION = "e6.v1"
DECISION_FILENAME = "_relative_buy_decision.json"
MODE_SHADOW = "shadow"

# ── v1 锁定的常量(改这里 = 改规则 = 必须换 RULE_VERSION 并走 registry)────────
#: 第 2 只 BUY 的已验证阈值。影子期**没有**——所以第 2 只恒不出。
SECOND_BUY_THRESHOLD: float | None = None
SECOND_BUY_BLOCK_REASON = "v1 影子期无已验证阈值"
#: `expected_abs_gap` 需要的最小 OOS 样本数;不足 → UNMEASURED,禁止拍数。
EXPECTED_ABS_GAP_MIN_N = 20
#: 成交额分位下限(L0 可交易全集内)。
LIQUIDITY_PCTL_FLOOR = 0.10
#: 硬门 ④ 的红灯停因。"监管/审计红灯"不在 `l4/parsers.py` 的七词表内(逐字实现,现行永不命中)。
REDFLAG_EARLY_STOP_REASONS = frozenset({"基本面恶化", "监管/审计红灯"})
#: `risk_safety` 里算作"风险类"的停因 = 七词表减去 {数据不足, 其他}(那两个是"缺证据/
#: 未归类",不是实质负面发现,且已由 evidence 面的早停基础分反映)。
RISK_EARLY_STOP_REASONS = frozenset({
    "涨停追高", "题材透支", "资金流出", "估值透支", "基本面恶化",
})
#: ST / 退市标记(镜像 `factor_lab.py` 与 `akshare_universe.py` 的同一判据)。
_ST_MARKS = ("ST", "退")

_HARD_GATES = ("tradable", "data_a", "contract", "no_redflag")
_FACES = ("target_align", "recall_strength", "evidence", "risk_safety")
_TRUTHY = {"true", "1", "yes", "是"}
_FALSY = {"false", "0", "no", "否"}


# ── 读取原语(容忍缺文件;不容忍猜)──────────────────────────────────────────
def _rows(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _json_doc(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _code(value: object) -> str:
    """`600188.SS` / `2345` / `002345` → `002345`(前导零唯一入口)。"""
    return str(value or "").strip().split(".")[0].zfill(6)


def _float(value: object) -> float | None:
    try:
        got = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if got != got else got


def _is_st(name: object) -> bool:
    text = str(name or "")
    return "ST" in text.upper() or "退" in text


def _entry_flag(value: object, *, default: bool = True) -> bool:
    """入场旗单元格 → bool。列缺席/单元格解析不出真值 → `default`(ruler 同款语义)。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return default


def _mid_rank_pctl(values: dict[str, float]) -> dict[str, float]:
    """{key: 原始值} → {key: 中位分位}。并列同值(公平且确定),n=1 → 0.5。

    用中位经验 CDF 而不是 `rank/n`:后者让并列票拿到不同分位,同分不同命,还会让
    "谁先被遍历到"渗进结果里——确定性就从这种地方漏掉。
    """
    n = len(values)
    if not n:
        return {}
    ordered = sorted(values.values())
    out: dict[str, float] = {}
    for key, value in values.items():
        less = bisect_left(ordered, value)
        equal = bisect_right(ordered, value) - less
        out[key] = round((less + 0.5 * equal) / n, 6)
    return out


def _blend(parts: list[tuple[float, float]]) -> float | None:
    """[(值, 权重)] → 加权均值;权重在**可得**分量间重新归一。全缺 → None。"""
    if not parts:
        return None
    total = sum(weight for _value, weight in parts)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in parts) / total


# ── L0 可交易全集(基准人口 + 流动性分位的唯一分母)─────────────────────────
def _universe(scan: Path) -> dict:
    """`L1_scored_full.csv` → 可交易全集 + 排除计数 + 逐票成交额/行业。

    基准分母 = **可交易**票,不是全市场所有行(T22 `retro._rel_gap_cols` 同款口径:含
    停牌 / 买不进的票会把"市场平均"算成不可执行的幻觉基准)。
    """
    rows = _rows(scan / "L1_scored_full.csv")
    flag_col = entry_flag_for()
    has_flag_col = bool(rows) and flag_col in (rows[0].keys() if rows else ())
    members: list[str] = []
    amount: dict[str, float | None] = {}
    industry: dict[str, str] = {}
    excluded = {"st_or_delisting": 0, "entry_flag_false": 0}
    for row in rows or []:
        code = _code(row.get("code"))
        amount[code] = _float(row.get("amount_yi"))
        industry[code] = str(row.get("industry") or "")
        if _is_st(row.get("name")):
            excluded["st_or_delisting"] += 1
            continue
        if has_flag_col and not _entry_flag(row.get(flag_col)):
            excluded["entry_flag_false"] += 1
            continue
        members.append(code)
    members = sorted(set(members))
    amounts = {code: amount[code] for code in members if amount.get(code) is not None}
    return {
        "members": members,
        "member_set": set(members),
        "amount": amount,
        "amount_pctl": _mid_rank_pctl(amounts),
        "industry": industry,
        "sectors": sorted({industry.get(code, "") for code in members} - {""}),
        "excluded": excluded,
        "entry_flag_column": flag_col,
        "entry_flag_present": has_flag_col,
        "rows_present": rows is not None,
    }


def tradable_universe(scan_dir: Path | str) -> list[str]:
    """当日 L0 可交易全集(排序后的 6 位代码)—— 相对基准的人口。"""
    return _universe(Path(scan_dir))["members"]


# ── 日级 A 级契约判定 ───────────────────────────────────────────────────────
def _data_contract_ok(scan: Path) -> tuple[bool, str]:
    health = _json_doc(scan / "run_health.json")
    if not isinstance(health, dict):
        return False, "run_health.json 缺失,A 级数据契约无从判定"
    if health.get("core_missing"):
        return False, f"core_missing={sorted(health['core_missing'])}"
    contract = health.get("run_contract") or {}
    if str(contract.get("status") or "") != "OK":
        return False, f"run_contract.status={contract.get('status')!r}"
    stages = health.get("stage_results") or {}
    if str(stages.get("status") or "") == "INVALID":
        return False, "stage_results.status=INVALID"
    if stages.get("failed"):
        return False, f"stage_results.failed={sorted(stages['failed'])}"
    records = health.get("decision_records") or {}
    if str(records.get("status") or "") in {"INVALID", "MISMATCH"}:
        return False, f"decision_records.status={records.get('status')!r}"
    return True, ""


# ── 逐票契约(task-book + 价格断言)─────────────────────────────────────────
def _task_book(scan: Path) -> dict | None:
    book = _json_doc(scan / "_l4_tasks.json")
    if not isinstance(book, dict) or not isinstance(book.get("tasks"), dict):
        return None
    return {_code(code): task for code, task in book["tasks"].items()
            if isinstance(task, dict)}


def _price_claim_status(scan: Path) -> dict[str, str]:
    """presence-gated:没有逐票产物就是 `UNMEASURED`,不猜、不当 fail。"""
    doc = _json_doc(scan / "_price_claim_status.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, str] = {}
    for code, entry in doc.items():
        if isinstance(entry, dict):
            mismatch = entry.get("n_mismatch")
            out[_code(code)] = "MISMATCH" if mismatch else "CLEAN"
        elif isinstance(entry, str):
            out[_code(code)] = entry.strip().upper()
    return out


def _tripwire_severity(scan: Path) -> dict[str, int]:
    """`_tripwire_conflicts.json` → 逐票冲突条数(v1 严重度 = 命中条数)。

    ⚠️ premise 偏差:该产物**没有** severity 字段,且只覆盖保送(pinned)票。v1 拿
    `all_hits` 的条数当严重度,非保送票恒 0。
    """
    doc = _json_doc(scan / "_tripwire_conflicts.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, int] = {}
    for code, entry in doc.items():
        hits = entry.get("all_hits") if isinstance(entry, dict) else None
        out[_code(code)] = len(hits) if isinstance(hits, list) else 0
    return out


# ── 四个面的原始分 ──────────────────────────────────────────────────────────
def _raw_faces(entry: dict, ctx: dict) -> dict[str, float | None]:
    """一只候选的四面**原始**分(尚未转当日候选内分位)。缺 → None。"""
    recall, card = entry["recall"], entry["l4"]
    code = entry["code"]

    target = recall.get("composite_pctl")

    channel_pctls = [chan.get("pctl") for chan in (recall.get("per_channel") or {}).values()
                     if chan.get("pctl") is not None]
    parts: list[tuple[float, float]] = []
    n_channels_pctl = ctx["n_channels_pctl"].get(code)
    if n_channels_pctl is not None:
        parts.append((n_channels_pctl, 0.5))
    if channel_pctls:
        parts.append((max(channel_pctls), 0.5))
    strength = _blend(parts)

    kind = card.get("card_kind")
    if kind is None:
        evidence = None
    else:
        evidence = 1.0 if kind == "full" else 0.4
        if card.get("intel_avail") == "INTEL":
            evidence += 0.2
        if code in ctx["dossier"]:
            evidence += 0.2
        if ctx["price_claim"].get(code) == "CLEAN":
            evidence += 0.2
        evidence = min(1.0, max(0.0, evidence))

    if not card.get("carded"):
        risk = None
    else:
        risk = float(len(card.get("risk_flags") or []))
        if str(card.get("earlystop_reason") or "") in RISK_EARLY_STOP_REASONS:
            risk += 1.0
        risk += float(ctx["tripwire"].get(code, 0))

    return {"target_align": target, "recall_strength": strength,
            "evidence": evidence, "risk_safety_risk": risk}


def _faces_table(entries: list[dict], ctx: dict) -> dict[str, dict]:
    """全体候选 → {code: {"faces": {...}, "missing": [...]}}(面内缺失 = 0.5 + 记账)。"""
    raws = {entry["code"]: _raw_faces(entry, ctx) for entry in entries}

    # risk 先归一(除以候选内最大风险),再取 safety = 1 − 归一风险
    risks = {code: raw["risk_safety_risk"] for code, raw in raws.items()
             if raw["risk_safety_risk"] is not None}
    worst = max(risks.values()) if risks else 0.0
    for raw in raws.values():
        risk = raw.pop("risk_safety_risk")
        raw["risk_safety"] = None if risk is None else (
            1.0 if worst <= 0 else 1.0 - risk / worst)

    out: dict[str, dict] = {code: {"faces": {}, "missing": []} for code in raws}
    for face in _FACES:
        defined = {code: raw[face] for code, raw in raws.items() if raw[face] is not None}
        pctls = _mid_rank_pctl(defined)
        for code in raws:
            if code in pctls:
                out[code]["faces"][face] = pctls[code]
            else:
                out[code]["faces"][face] = 0.5
                out[code]["missing"].append(face)
    for record in out.values():
        record["missing"].sort()
    return out


# ── 硬资格四类 ─────────────────────────────────────────────────────────────
def _hard_gate(entry: dict, ctx: dict) -> tuple[dict[str, bool], list[dict]]:
    """一只候选的四门 + 失败明细(一门一行,便于 `blocked_reasons` 分桶)。"""
    code = entry["code"]
    gates: dict[str, bool] = {}
    details: list[dict] = []

    def fail(gate: str, detail: str) -> None:
        gates[gate] = False
        details.append({"code": code, "reason": f"hard_gate.{gate}", "detail": detail})

    # ① 入场可交易
    if code not in ctx["universe"]["member_set"]:
        if code not in ctx["universe"]["amount"]:
            fail("tradable", "不在当日 L0 可交易全集(L1_scored_full.csv)")
        elif _is_st(entry.get("name")):
            fail("tradable", "ST/退市标记")
        else:
            fail("tradable", f"入场旗 {ctx['universe']['entry_flag_column']} 为假")
    else:
        gates["tradable"] = True

    # ② A 级数据契约(日级)
    if ctx["data_a"][0]:
        gates["data_a"] = True
    else:
        fail("data_a", ctx["data_a"][1])

    # ③ slim / 卡 / 价格断言契约
    book = ctx["task_book"]
    claim = ctx["price_claim"].get(code, "UNMEASURED")
    if book is None:
        fail("contract", "task-book(_l4_tasks.json)缺失,卡契约无从判定")
    elif code not in book:
        fail("contract", "task-book 无该票记录")
    else:
        task = book[code]
        artifacts = task.get("artifacts") or {}
        status = str(task.get("status") or "")
        missing = [name for name in ("slim", "card")
                   if str((artifacts.get(name) or {}).get("status") or "") != "PRESENT"]
        if status != "SUCCEEDED":
            fail("contract", f"task-book status={status!r}(非 SUCCEEDED)")
        elif missing:
            fail("contract", f"产物缺席:{'/'.join(missing)}")
        elif claim == "MISMATCH":
            fail("contract", "价格断言与 OHLCV 不符(price_claim=MISMATCH)")
        else:
            gates["contract"] = True

    # ④ 无红灯
    rating = entry["l4"].get("research_rating")
    stop_reason = str(entry["l4"].get("earlystop_reason") or "")
    amount_pctl = ctx["universe"]["amount_pctl"].get(code)
    if _is_st(entry.get("name")):
        fail("no_redflag", f"ST/退市标记:{entry.get('name')!r}")
    elif rating == "Sell":
        fail("no_redflag", "research_rating=Sell")
    elif stop_reason in REDFLAG_EARLY_STOP_REASONS:
        fail("no_redflag", f"早停红灯停因:{stop_reason}")
    elif amount_pctl is not None and amount_pctl < LIQUIDITY_PCTL_FLOOR:
        fail("no_redflag",
             f"成交额分位 {amount_pctl:.4f} < P{int(LIQUIDITY_PCTL_FLOOR * 100)}")
    else:
        gates["no_redflag"] = True

    return {gate: gates.get(gate, False) for gate in _HARD_GATES}, details


# ── 主构建 ─────────────────────────────────────────────────────────────────
def build_decision(scan_dir: Path | str, date: str | None = None,
                   mode: str = MODE_SHADOW) -> dict:
    """`context/scan/<date>` → 统一相对决策文档(确定性、零 LLM、零联网、只读)。

    护照**现算**(`passport.build_passport`),不读盘上那份 `_candidate_passport.json`:
    它是同一函数的派生视图,现算才保证决策与当日真实产物同源,不被一份过期文件摆布。
    """
    if mode != MODE_SHADOW:
        raise ValueError(
            f"v1 只接受 mode={MODE_SHADOW!r}(活体切换是独立的 GATED task);收到 {mode!r}")
    scan = Path(scan_dir)
    date = date or scan.name

    passport = build_passport(scan)
    entries = [entry for entry in passport["candidates"].values()
               if entry["l4"].get("dispatched")]
    entries.sort(key=lambda entry: entry["code"])

    universe = _universe(scan)
    dossier = _json_doc(scan / "_dossier_present.json")
    n_channels = {entry["code"]: entry["recall"].get("n_channels")
                  for entry in entries
                  if entry["recall"].get("n_channels") is not None}
    ctx = {
        "universe": universe,
        "data_a": _data_contract_ok(scan),
        "task_book": _task_book(scan),
        "price_claim": _price_claim_status(scan),
        "tripwire": _tripwire_severity(scan),
        "dossier": {_code(code) for code in dossier} if isinstance(dossier, list) else set(),
        "n_channels_pctl": _mid_rank_pctl({code: float(value)
                                           for code, value in n_channels.items()}),
    }

    faces = _faces_table(entries, ctx)
    excluded: list[dict] = []
    candidates: list[dict] = []
    for entry in entries:
        code = entry["code"]
        gates, details = _hard_gate(entry, ctx)
        excluded.extend(details)
        face = faces[code]["faces"]
        candidates.append({
            "code": code,
            "name": entry.get("name"),
            "sector": entry.get("sector"),
            "pinned": bool(entry["l2"].get("pinned")),
            "eligible": all(gates.values()),
            "hard_gate": gates,
            "faces": {name: face[name] for name in _FACES},
            "faces_missing": faces[code]["missing"],
            "relative_decision_score": round(
                sum(face[name] for name in _FACES) / len(_FACES), 6),
            "rank": None,
            "research_rating": entry["l4"].get("research_rating"),
            "amount_pctl": universe["amount_pctl"].get(code),
            "price_claim": ctx["price_claim"].get(code, "UNMEASURED"),
            "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0},
        })

    by_code = {row["code"]: row for row in candidates}
    eligible = sorted(
        (row for row in candidates if row["eligible"]),
        key=lambda row: (-row["relative_decision_score"],
                         -row["faces"]["target_align"],
                         -(universe["amount"].get(row["code"]) or 0.0),
                         row["code"]))
    for rank, row in enumerate(eligible, start=1):
        by_code[row["code"]]["rank"] = rank

    # 第 2 只起的门:v1 影子期无已验证阈值 → 恒不满足,恒只出 1 只。
    buys = ([{"code": eligible[0]["code"], "basis": "relative", "rank": 1}]
            if eligible else [])
    second_buy = {"fired": SECOND_BUY_THRESHOLD is not None,
                  "reason": SECOND_BUY_BLOCK_REASON,
                  "threshold": SECOND_BUY_THRESHOLD}

    blocked = not buys
    blocked_reasons: list[dict] = []
    if blocked:
        if not candidates:
            blocked_reasons = [{"reason": "no_candidates", "n": 0}]
        else:
            buckets: dict[str, int] = {}
            for row in excluded:
                buckets[row["reason"]] = buckets.get(row["reason"], 0) + 1
            blocked_reasons = [{"reason": reason, "n": buckets[reason]}
                               for reason in sorted(buckets)]

    market_members = universe["members"]
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "mode": mode,
        "date": date,
        "ruler": MAIN_RULER,
        "benchmark": {
            "ruler": MAIN_RULER,
            "entry_flag": universe["entry_flag_column"],
            "entry_flag_present": universe["entry_flag_present"],
            "market": {
                "definition": "L0 可交易全集等权 gap_c1_o2",
                "column": REL_MARKET,
                "n": len(market_members),
                "members_sha256": hashlib.sha256(
                    "\n".join(market_members).encode("utf-8")).hexdigest(),
            },
            "sector": {
                "definition": "申万一级可交易等权",
                "column": REL_SECTOR,
                "source_column": "industry",     # 实为 tushare「所处行业」,见文件尾 ⑤
                "n_sectors": len(universe["sectors"]),
            },
            "excluded_from_benchmark": universe["excluded"],
        },
        "inputs": {
            "l1_scored_full": "PRESENT" if universe["rows_present"] else "ABSENT",
            "task_book": "PRESENT" if ctx["task_book"] is not None else "ABSENT",
            "price_claim_status": ("PRESENT" if ctx["price_claim"] else "ABSENT"),
            "dossier_present": "PRESENT" if ctx["dossier"] else "ABSENT",
            "run_health": ("PRESENT" if (scan / "run_health.json").exists()
                           else "ABSENT"),
        },
        "counts": {
            "candidates": len(candidates),
            "eligible": len(eligible),
            "excluded_rows": len(excluded),
            "buys": len(buys),
            "with_missing_face": sum(1 for row in candidates if row["faces_missing"]),
        },
        "candidates": candidates,
        "buys": buys,
        "second_buy": second_buy,
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
        "excluded": sorted(excluded, key=lambda row: (row["code"], row["reason"])),
    }


def write_decision(scan_dir: Path | str, date: str | None = None,
                   mode: str = MODE_SHADOW) -> Path:
    """构建并原子落盘。`sort_keys=True` 是 byte 稳定契约的一半,另一半是构建本身无时序量。"""
    scan = Path(scan_dir)
    target = scan / DECISION_FILENAME
    payload = json.dumps(build_decision(scan, date, mode), ensure_ascii=False,
                         indent=2, sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(target)
    return target


def safe_write_decision(scan_dir: Path | str, date: str | None = None) -> Path | None:
    """影子件失败不得阻断任何东西(本轮没有任何生产消费者依赖它)。"""
    try:
        return write_decision(scan_dir, date)
    except Exception as exc:  # noqa: BLE001 — 纯影子件失败只记一行,不连累主链
        print(f"[relative_buy] 构建失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="统一相对决策层 finalizer v1(影子;确定性、零 LLM、零联网)")
    parser.add_argument("scan", help="分析日(YYYY-MM-DD)或 scan 目录")
    args = parser.parse_args(argv)
    explicit = Path(args.scan)
    scan = explicit if explicit.exists() else Path("context/scan") / args.scan
    target = write_decision(scan)
    doc = json.loads(target.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "path": str(target), "mode": doc["mode"],
                      "rule_version": doc["rule_version"], "counts": doc["counts"],
                      "buys": doc["buys"], "blocked": doc["blocked"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


# ── v1 已知问题(不在本轮改;改 = 新 rule_version + registry)────────────────
#
# 1. `REDFLAG_EARLY_STOP_REASONS` 里的 `"监管/审计红灯"` 在现行早停词表(`l4/parsers.py`
#    七选一)下**永不命中**——这一支是死条件。真正的监管信号在 L3 侧另有独立探测器
#    (`agents/l3_news.reg_flag`,近 10 日公告命中 立案/问询/关注函/处罚/违规/诉讼/监管/
#    证监会/交易所),但它没有进 L4 的结构化产物。要让这一支活起来,得先把 `news_reg`
#    接进逐票产物,那是另一件事。
# 2. 逐票 `price_claim` 状态没有生产者(见文件头 ③)。在补上 `_price_claim_status.json`
#    的生产者之前,`evidence` 面的 "+0.2 干净" 分量对全体候选恒不给分 = 零鉴别力,
#    硬门 ③ 的价格断言这一支同样恒不触发。
# 3. `tripwire` 严重度只覆盖保送(pinned)票,非保送票恒 0。
# 4. 保送(pinned)持仓**不**被排除在 BUY 候选之外(v1 规则没这一条)。若某天相对 BUY
#    落在自己已持有的票上,读数会与"新开仓"混在一起——`candidates[].pinned` 已逐票留痕,
#    影子期用它做分层统计即可,不改规则(retro 的 L3 edge 曾被📌保送污染,同族前科)。
# 5. 行业基准的口径名叫"申万一级",但可用的 `industry` 列是 tushare「所处行业」
#    (2026-08-06 实测 129 个组、带 "Ⅱ" 后缀 = 申万二级粒度)。这个名实不符**不是本模块
#    引入的**:T22 `retro._rel_gap_cols` 生产 `rel_gap_sector` 时按同一列分组、docstring
#    也写"申万一级"。两处必须同时改才有意义,故本轮只把 `source_column` 落进产物留痕。
# 6. `research_rating != "Sell"` 只挡五档里最末一档。实测 2026-08-06 有 4 只 `Underweight`
#    (`decision_records.proposal == "SELL"`)全部通过硬门并进入排序——当天冠军是 Hold 所以
#    没咬到,但"提议卖出的票可以当相对 BUY 出"这条通路是敞开的。规则观察前锁定,本轮不改。
if __name__ == "__main__":
    raise SystemExit(main())
