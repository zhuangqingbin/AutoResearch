#!/usr/bin/env python3
"""相对 BUY 影子账本(Wave12 T24 / E6-2)—— 前向观测 + 三尺同屏(确定性,零 LLM,零联网)。

design: Wave12 E6(用户 2026-08-08 裁定)。上游 = T23 决策层
`autoresearch/scan/relative_buy.py` 的逐日产物 `_relative_buy_decision.json`;下游 =
`context/learning/relative_buy.jsonl` + `reports/learning/relative_buy.md`。

## 它记的是什么

系统对外**只有一个** `BUY`。每个成功完成的交易日至少给出一只,最低那只是**相对 BUY**
——"在今日可交易全集里相对最值得买",**不承诺绝对上涨**。所以这本账天生要同时看三把尺:

| 尺 | 列 | 含义 |
|---|---|---|
| 绝对 | `gap_c1_o2` | open[D+2]/close[D+1]−1,T+1 收盘买 → T+2 开盘卖的真实隔夜收益 |
| 相对(主) | `rel_gap_market` | 同日全市场可交易等权均值的超额 |
| 相对(辅) | `rel_gap_sector` | 同申万一级可交易等权均值的超额 |

**绝对为负 ≠ 决策错**:相对 BUY 的承诺是"在当日全集里相对最优",弱市里它可以既是当日
最优、又是绝对亏损。报表对这种行**必须**渲染「弱市相对最优」(`WEAK_MARKET_NOTE`),
不得改写成绝对看涨——这是本模块唯一一条会主动"给自己难堪"的渲染规则,也是它存在的
理由之一。

## 本轮仍是影子

只写自己这两份产物:**不写生产 buy ledger、不改 publisher、不碰 `decision_records.json`、
不改任何评级**。活体切换是独立的 GATED task(≥20 个决策日影子 + 五守卫 + 人批),登记在
`experiment_registry` 的 `exp_relative_buy_owner`(family=decision,PREREGISTERED)。

## 不与旧账混算(定义断层)

旧 OW 买单账(`buy_ledger`,历史 9 笔、胜率 0%)与本账**分列并置,不连成一条趋势线**:
①决策对象不同(绝对"值得买" vs 相对"最值得买");②人口不同(≥Overweight 的卡 vs 当日
全部 L4 候选的相对冠军)。把两段接成一条曲线会读出一个从来没存在过的"改善"。

**勘误(2026-08-09 复核 M-5)**:此处原先还写着"③尺不同(旧行 `fwd_2` 在 T16 换尺前后
混口径)"—— **不成立**。`buy_ledger.roll()` 的 `fwd_2` 是 `_a(MAIN_RULER)` 每次现算
(`buy_ledger.py:168`;该账没有持久化 CSV,每次 roll 都从 attribution 重新读),三条已
实现旧行与本账同为 `gap_c1_o2`。结论不变但理由要对:两账**可比但不可续** —— 同一把尺,
量的是两个不同的决策问题。

## 幂等

`roll()` 是**按日期键的整替**:每次全量重建当前可见的决策日,与既有账本按 `date` 合并
——同日覆写、旧日保留(决策文件被清掉不等于那天没发生过)、不追加重复行。同输入重复跑
byte 稳定:不写时间戳、不写随机序,`sort_keys=True`,浮点一律 `round(…, 6)`。

  uv run --no-sync python -m autoresearch.learning.relative_ledger
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import REL_GAP_RULER, REL_MARKET, REL_SECTOR
from autoresearch.scan.relative_buy import DECISION_FILENAME, MODE_ACTIVE, MODE_SHADOW

#: 账本合法的 `mode` 值——v2.0(task-2.2)前只认 `MODE_SHADOW`;U6 用户裁定「转正后记
#: 分册就是这本账本」,active 期必须能继续记账,不被契约校验拒收。
_LEGAL_MODES = frozenset({MODE_SHADOW, MODE_ACTIVE})

SCHEMA_VERSION = 1
BASIS = "relative"
SCAN_ROOT = ws.scan_root()
LEDGER_PATH = ws.context_root() / "learning/relative_buy.jsonl"
REPORT_PATH = ws.reports_root() / "learning/relative_buy.md"

#: 成熟门:≥20 个决策日,**自首条观测起算**(不是从预注册日起算)。
#: EXP-1/EXP-2 预注册后数据腿从未实现、observations 空转 6 天(FN-1 家族)——按"预注册
#: 日 +20 天"计门会让一条从没产生过观测的腿在第 21 天自动"成熟"。
MATURE_MIN_OBSERVATIONS = 20

#: 绝对 gap 为负的行的固定文案。改这句 = 改本账对外的诚实边界,必须有测试跟着变红。
WEAK_MARKET_NOTE = "弱市相对最优(绝对 gap 为负;相对 BUY 从不承诺绝对收益为正)"

#: 主尺的实现窗口,写进每行 `outcome.as_of`(as-of 是"这个数在哪一刻才存在",不是跑批时间)。
RULER_WINDOW = "open[D+2]/close[D+1]-1"

#: 已登记观测被重算结果改写时的契约错前缀(见 `_freeze`)。前缀固定 = 幂等去重的键。
FROZEN_ERROR_PREFIX = "已登记观测被重算结果改写(已拒绝)"

#: 「决策身份」= 这一行记的**是哪一个决策**。`roll()` 拿它判断重算结果与已登记的那条
#: 是不是同一件事;不同 → 保留已登记的那条(见 `_freeze`)。
#: 只收**决策期**就已定死的字段;`outcome` 不在内(前向读数本来就该随时间成熟)。
_DECISION_IDENTITY_FIELDS = ("rule_version", "mode", "status", "code", "n_buys",
                             "relative_decision_score", "faces", "faces_missing",
                             "n_candidates", "n_eligible")

_STATUS_BUY = "BUY"
_STATUS_BLOCKED = "BLOCKED"
_STATUS_NO_RUN = "NO_RUN"
_FACES = ("target_align", "recall_strength", "evidence", "risk_safety")
_FACE_LABELS = {"target_align": "目标", "recall_strength": "召回",
                "evidence": "证据", "risk_safety": "风险"}


# ── 原语 ───────────────────────────────────────────────────────────────────
def _code(value: object) -> str:
    """`600188.SS` / `2345` → `002345`(前导零唯一入口;每个 join 处都经它)。"""
    return str(value or "").strip().split(".")[0].zfill(6)


def _round(value: object) -> float | None:
    try:
        got = float(value)
    except (TypeError, ValueError):
        return None
    return None if got != got else round(got, 6)


def _json_doc(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _scan_days(scan_root: Path) -> list[Path]:
    if not scan_root.exists():
        return []
    return sorted((p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"),
                  key=lambda p: p.name)


# ── 契约校验(账本不照单全收上游)──────────────────────────────────────────
def _contract_errors(doc: dict) -> list[str]:
    """决策文档 → 契约违约清单(空 = 合规)。registry 的 `contract_error_n` 守卫读它。

    账本对上游做校验不是不信任,是因为**只有账本这一侧能把违约留成时间序列**:
    finalizer 抛异常会被 `safe_write_decision` 吞成一行 stderr,过后谁也说不清哪天错过。
    """
    errors: list[str] = []
    if str(doc.get("mode") or "") not in _LEGAL_MODES:
        errors.append(f"mode={doc.get('mode')!r} 不在合法集合 {sorted(_LEGAL_MODES)} 内"
                      f"(账本只认 {MODE_SHADOW}/{MODE_ACTIVE} 两态)")
    buys = doc.get("buys") or []
    if len(buys) > 1:
        errors.append(f"v1 契约是每日恰 1 只 BUY(第 2 只恒不出),实收 {len(buys)} 只")
    for buy in buys:
        if str((buy or {}).get("basis") or "") != BASIS:
            errors.append(f"BUY basis={buy.get('basis')!r} 非 {BASIS}")
    if str(doc.get("ruler") or "") != REL_GAP_RULER:
        errors.append(f"ruler={doc.get('ruler')!r} 非 {REL_GAP_RULER}")
    codes = {_code(row.get("code")) for row in (doc.get("candidates") or [])}
    for buy in buys:
        if _code((buy or {}).get("code")) not in codes:
            errors.append(f"BUY {buy.get('code')!r} 不在候选表内")
    return errors


#: `contract_error_kind()` 返回值:结构性契约违规(preflight 读它标「真」,需人核实)。
CONTRACT_ERROR_KIND_REAL = "real"
#: `contract_error_kind()` 返回值:I-6 冻结的版本时序留痕(preflight 读它标「良性」)。
CONTRACT_ERROR_KIND_VERSION_SKEW_SUPERSEDE = "version_skew_supersede"
#: `contract_error_kind()` 返回值:该行没有契约错。
CONTRACT_ERROR_KIND_NONE = "none"


def contract_error_kind(row: dict) -> str:
    """一行账的 `contract_errors` → `CONTRACT_ERROR_KIND_*` 之一。

    背景(2026-08-19 preflight 实测):I-6 冻结保护(`_freeze`)在 `rule_version` 升版时
    正确拒绝了重算结果覆写已登记观测,并把这一事实记成一条 `FROZEN_ERROR_PREFIX` 契约
    错——这是**治理留痕**,不是**真违规**:同一个 BUY 决策没有变,只是版本标签升了(真实
    账本 `n_contract_errors=8` 全部同族:`e6.v1.1`→`e6.v2.0`,BUY 代码逐条相同)。但它与
    结构性契约违规(mode 非法/basis 错/同日多只 BUY/BUY 不在候选表内,见 `_contract_errors`)
    混进同一个计数,会让转正前人读体检看起来像一堆吓人的假警报——这个函数就是拆开这
    两类的判据(preflight 消费它,不接线进任何自动化门)。

    判 `CONTRACT_ERROR_KIND_VERSION_SKEW_SUPERSEDE`(良性)当且仅当:
    - 该行**全部**契约错误都带 `FROZEN_ERROR_PREFIX` 前缀(没有夹杂别的违规);
    - 且 `superseded` 在场、`recorded_code == recomputed_code`(BUY 决策本身没变,只是
      `rule_version` 换了标签)。

    否则判 `CONTRACT_ERROR_KIND_REAL`,包括两种情形:①任何非冻结类契约错(结构性违规,
    `_contract_errors` 产的那些);②冻结了、但 BUY 代码本身也变了(重算换了一只票,如
    `test_rule_version_change_does_not_rewrite_a_recorded_observation`)——那才是真正
    可能丢观测、需要人工核实的情形。`superseded` 缺失/不是 dict 时同样判 `REAL`:拿不出
    「代码没变」的证据就不能算良性(宁可假阳,不可假阴)。
    """
    errors = row.get("contract_errors") or []
    if not errors:
        return CONTRACT_ERROR_KIND_NONE
    if not all(str(error).startswith(FROZEN_ERROR_PREFIX) for error in errors):
        return CONTRACT_ERROR_KIND_REAL
    superseded = row.get("superseded")
    if not isinstance(superseded, dict) or not superseded:
        return CONTRACT_ERROR_KIND_REAL
    if _code(superseded.get("recorded_code")) != _code(superseded.get("recomputed_code")):
        return CONTRACT_ERROR_KIND_REAL
    return CONTRACT_ERROR_KIND_VERSION_SKEW_SUPERSEDE


def _status(doc: dict) -> str:
    """BUY / BLOCKED / NO_RUN。

    `NO_RUN` 不是 `BLOCKED` 的别名:目录在、但当天压根没跑 scan(2026-08-07 实况——只有
    `_prewarm.json`)。把它记成 BLOCKED 会把 action coverage 的分母灌水,让"没跑"看起来
    像"跑了但拦住了"。
    """
    if doc.get("buys"):
        return _STATUS_BUY
    counts = doc.get("counts") or {}
    inputs = doc.get("inputs") or {}
    ran = (int(counts.get("candidates") or 0) > 0
           or inputs.get("task_book") == "PRESENT"
           or inputs.get("l1_scored_full") == "PRESENT")
    return _STATUS_BLOCKED if ran else _STATUS_NO_RUN


# ── 成熟回填:三尺 ─────────────────────────────────────────────────────────
def outcome_for(date: str, code: str | None, scan_root: Path) -> dict:
    """该日该票的三尺读数。attribution 缺 / 主尺 NaN → `PENDING`(缺证据不等于 0)。

    `rel_gap_market` / `rel_gap_sector` 优先读盘上已有的两列(T22 `refresh_attributions`
    夜间回填);两列不在盘上时用 `retro._rel_gap_cols` **现算**——那是 T22 的同一个纯函数,
    刻意不在这里另写一份(两处各写一份只会在某一天被人发现已经漂了)。

    分母的均值不重算:由恒等式 `mean = gap − rel_market` 反解,拿到的正是**当时真用的那个
    基准**(盘上两列若来自更早一次回填,重算会给出一个从没被用过的分母)。`benchmark_n`
    是同一池子的计数(`entry_tradable ∧ 主尺有数`),口径与 `_rel_gap_cols` 完全一致。
    """
    source = f"{date}/retro/attribution.csv"
    as_of = {"ruler": REL_GAP_RULER, "window": RULER_WINDOW,
             "basis_date": date, "source": source}
    if not code:
        return {"status": "NA", "gap_c1_o2": None, "rel_gap_market": None,
                "rel_gap_sector": None, "market_mean_gap": None, "benchmark_n": None,
                "rel_source": "ABSENT", "as_of": as_of}
    blank = {"status": "PENDING", "gap_c1_o2": None, "rel_gap_market": None,
             "rel_gap_sector": None, "market_mean_gap": None, "benchmark_n": None,
             "rel_source": "ABSENT", "as_of": as_of}
    path = Path(scan_root) / date / "retro" / "attribution.csv"
    if not path.exists():
        return blank

    import pandas as pd

    from autoresearch.common.ruler import entry_tradable
    from autoresearch.learning.retro import _rel_gap_cols

    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏历史文件不该拖垮整本账
        return blank
    if "code" not in frame.columns or REL_GAP_RULER not in frame.columns:
        return blank
    frame["code"] = frame["code"].map(_code)
    on_disk = REL_MARKET in frame.columns and REL_SECTOR in frame.columns
    # M-13 修复(2026-08-09 复核):缺 `industry` 只让**相对两列**算不出来,绝对主尺
    # 照样成熟。修复前这里整行 `return blank` —— 已经在盘上的 `gap_c1_o2` 被一起丢掉,
    # 外观与"还没到 T+2"逐字节相同,读者会以为再等一天就有(其实永远不会有)。
    # 把"行业列缺失"伪装成"数据未成熟"是本仓库最忌讳的名实不符。
    rel_source = "ON_DISK" if on_disk else "COMPUTED"
    if not on_disk:
        if "industry" not in frame.columns:
            rel_source = "NO_INDUSTRY_COLUMN"
        else:
            frame[REL_MARKET], frame[REL_SECTOR] = _rel_gap_cols(frame)
    row = frame[frame["code"] == _code(code)]
    if row.empty:
        return blank
    gap = _round(row.iloc[0][REL_GAP_RULER])
    if gap is None:
        return blank
    degraded = rel_source == "NO_INDUSTRY_COLUMN"
    rel_market = None if degraded else _round(row.iloc[0][REL_MARKET])
    rel_sector = None if degraded else _round(row.iloc[0][REL_SECTOR])
    gaps = pd.to_numeric(frame[REL_GAP_RULER], errors="coerce")
    pool = entry_tradable(frame, ruler_name=REL_GAP_RULER) & gaps.notna()
    return {
        "status": "MATURE",
        "gap_c1_o2": gap,
        "rel_gap_market": rel_market,
        "rel_gap_sector": rel_sector,
        "market_mean_gap": None if rel_market is None else _round(gap - rel_market),
        "benchmark_n": int(pool.sum()),
        "rel_source": rel_source,
        "as_of": as_of,
    }


# ── 一份决策文档 → 一行账 ──────────────────────────────────────────────────
def row_from_decision(doc: dict, scan_root: Path | str | None = None) -> dict:
    """纯派生:决策文档(+ 成熟后的 attribution)→ 一行账。无时序量,byte 稳定。"""
    scan = Path(scan_root or SCAN_ROOT)
    date = str(doc.get("date") or "")
    buys = doc.get("buys") or []
    by_code = {_code(row.get("code")): row for row in (doc.get("candidates") or [])}
    status = _status(doc)
    code = _code(buys[0].get("code")) if buys else None
    pick = by_code.get(code) if code else None
    counts = doc.get("counts") or {}
    benchmark = doc.get("benchmark") or {}
    market = benchmark.get("market") or {}
    sector = benchmark.get("sector") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "rule_version": doc.get("rule_version"),
        "mode": doc.get("mode"),
        "basis": BASIS,                        # 全行恒 relative(不存在第二种 basis)
        "ruler": REL_GAP_RULER,
        "status": status,
        "n_buys": len(buys),
        "code": code,
        "name": (pick or {}).get("name"),
        "sector": (pick or {}).get("sector"),
        "pinned": bool((pick or {}).get("pinned")),
        "rank": (pick or {}).get("rank"),
        "research_rating": (pick or {}).get("research_rating"),
        "relative_decision_score": _round((pick or {}).get("relative_decision_score")),
        "faces": {face: _round(((pick or {}).get("faces") or {}).get(face))
                  for face in _FACES} if pick else {},
        "faces_missing": sorted((pick or {}).get("faces_missing") or []),
        "n_candidates": int(counts.get("candidates") or 0),
        "n_eligible": int(counts.get("eligible") or 0),
        "blocked_reasons": doc.get("blocked_reasons") or [],
        "benchmark": {
            "market_n": market.get("n"),
            "market_column": market.get("column", REL_MARKET),
            "members_sha256": market.get("members_sha256"),
            "n_sectors": sector.get("n_sectors"),
            "sector_column": sector.get("column", REL_SECTOR),
            "entry_flag": benchmark.get("entry_flag"),
            "entry_flag_present": bool(benchmark.get("entry_flag_present")),
        },
        "contract_errors": _contract_errors(doc),
        "outcome": outcome_for(date, code, scan),
    }


# ── 账本读写(按 date 键整替)──────────────────────────────────────────────
def load_ledger(path: Path | str | None = None) -> list[dict]:
    target = Path(path or LEDGER_PATH)
    if not target.exists():
        return []
    rows: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                      # 坏行跳过,不让一行毁掉整本账
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_ledger(rows: list[dict], path: Path | str | None = None) -> Path:
    target = Path(path or LEDGER_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in rows)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(target)
    return target


def replay(scan_root: Path | str | None = None,
           days: list[str] | None = None) -> list[dict]:
    """历史回放:**只读**地对指定日跑 finalizer,不在 scan 目录留下任何文件。

    前向接线(`post_run`)只覆盖"以后跑的 run";已经跑过的历史日从来没写过决策文件,
    不回放的话这本账从上线第一天起就是空的——EXP-1/EXP-2 预注册后 observations 空转
    6 天,正是"腿建好了但没有任何东西喂它"(FN-1 家族)。
    """
    from autoresearch.scan.relative_buy import build_decision

    scan = Path(scan_root or SCAN_ROOT)
    names = list(days) if days is not None else [p.name for p in _scan_days(scan)]
    rows: list[dict] = []
    for name in sorted(names):
        try:
            doc = build_decision(scan / name, name)
        except Exception as exc:  # noqa: BLE001 — 单日坏产物不阻其余日
            print(f"[relative_ledger] 回放 {name} 跳过: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        rows.append(row_from_decision(doc, scan))
    return rows


def decision_identity(row: dict) -> str:
    """一行账的「决策身份」哈希 —— 同身份 = 同一个决策,可以放心用新算的那份覆盖。

    只吃 `_DECISION_IDENTITY_FIELDS`(决策当日就已定死的字段),**不吃 `outcome`**:
    前向读数从 PENDING 变 MATURE 是这条观测在按预期成熟,不是改写。

    对**已存在的老行**同样成立(直接读它自己的字段现算),所以不需要给账本加
    `decision_hash` 列、也不需要迁移 —— 新旧行走同一条算式。
    """
    payload = {name: row.get(name) for name in _DECISION_IDENTITY_FIELDS}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _freeze(prior: dict, fresh: dict, scan: Path) -> dict:
    """已登记观测 vs 重算结果不一致 → **保留已登记的那条**,把冲突写在明面上。

    I-6 修复(2026-08-09 复核;已实测发生过一次:底片产出于 `rule_version=e6.v1`,
    其后 finalizer 升 `e6.v1.1`,某晚 `roll()` 的回放把 8 行预注册期观测整体改写成
    v1.1)。这本账**唯一**的存在理由是给 `exp_relative_buy_owner` 攒可信观测,而
    registry 那条「definition hash 变了就必须换实验 id、不能边跑边改」的铁律,精神就是
    **已汇集的观测不可被悄悄改写** —— 否则将来看晋升证据的人分不清哪些数是原始记录、
    哪些是后来重跑覆盖的,spec 里「≥20 个决策日中至少 13 条产生于 register 之后」这条
    样本外承诺会静默失效。

    不拿"反正回放结果一样"放过:结果一样时没人发现,结果不一样时已经晚了。

    冻的是**决策**,不是**读数**:`outcome` 照常按冻结下来的那只票回填(把它一起冻住
    等于把观测腿掐死)。契约错按 `FROZEN_ERROR_PREFIX` 去重后重写,连跑 N 次不堆积。
    """
    kept = json.loads(json.dumps(prior, ensure_ascii=False))
    kept["outcome"] = outcome_for(str(kept.get("date") or ""), kept.get("code"), scan)
    kept["superseded"] = {
        "recorded_decision_hash": decision_identity(prior),
        "recomputed_decision_hash": decision_identity(fresh),
        "recorded_rule_version": prior.get("rule_version"),
        "recomputed_rule_version": fresh.get("rule_version"),
        "recorded_code": prior.get("code"),
        "recomputed_code": fresh.get("code"),
        "recomputed_status": fresh.get("status"),
        "recomputed_score": fresh.get("relative_decision_score"),
    }
    errors = [error for error in (prior.get("contract_errors") or [])
              if not str(error).startswith(FROZEN_ERROR_PREFIX)]
    errors.append(
        f"{FROZEN_ERROR_PREFIX}:已登记 rule_version={prior.get('rule_version')!r}"
        f"/BUY={prior.get('code')!r},重算得 {fresh.get('rule_version')!r}"
        f"/{fresh.get('code')!r} —— 保留已登记值,新结果只记在 `superseded`")
    kept["contract_errors"] = errors
    return kept


def roll(scan_root: Path | str | None = None, ledger_path: Path | str | None = None,
         *, replay_missing: bool = True, max_replay_days: int = 20) -> list[dict]:
    """全量重建 + 按 `date` 与既有账本合并 + 落盘。返回按日期排序的全部行。

    合并语义(幂等整替 + **观测冻结**):
    - 本次看不到的旧日 → 原样保留(「账本追加列不清零」同族:决策文件被清掉不等于
      那天没发生过);
    - 同日、`decision_identity` 相同 → 用本次重建的行(读数得以继续成熟);
    - 同日、`decision_identity` **不同** → **保留已登记的那条** + 记 `superseded` +
      记一条契约错(I-6,见 `_freeze`)。
    """
    scan = Path(scan_root or SCAN_ROOT)
    fresh: dict[str, dict] = {}
    replay_days: list[str] = []
    for day in _scan_days(scan):
        doc = _json_doc(day / DECISION_FILENAME)
        if isinstance(doc, dict):
            fresh[day.name] = row_from_decision(doc, scan)
        elif replay_missing and (day / "_l4_tasks.json").exists():
            replay_days.append(day.name)
    for row in replay(scan, replay_days[-max_replay_days:] if max_replay_days else replay_days):
        fresh[row["date"]] = row
    merged = {row.get("date"): row for row in load_ledger(ledger_path)}
    for date, row in fresh.items():
        prior = merged.get(date)
        merged[date] = (row if prior is None
                        or decision_identity(prior) == decision_identity(row)
                        else _freeze(prior, row, scan))
    rows = [merged[date] for date in sorted(merged) if date]
    write_ledger(rows, ledger_path)
    return rows


# ── 汇总 ───────────────────────────────────────────────────────────────────
def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "worst": None, "n_negative": 0}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    return {"n": len(ordered), "mean": round(sum(ordered) / len(ordered), 6),
            "median": round(median, 6), "worst": round(ordered[0], 6),
            "n_negative": sum(1 for value in ordered if value < 0)}


def summarize(rows: list[dict]) -> dict:
    """账本 → 守卫读数。`action_coverage` 的分母是**决策日**(BUY+BLOCKED),不含 NO_RUN。

    为什么不按任务书字面的"非 BLOCKED 日 BUY≥1 占比":那个式子恒等于 100%(非 BLOCKED
    按定义就是出了 BUY),是一条永远不会变红的守卫 = 没有守卫。这里把 BLOCKED 日留在分母
    里,coverage 才真的能掉下来。
    """
    decision = [row for row in rows if row.get("status") in (_STATUS_BUY, _STATUS_BLOCKED)]
    buy_days = [row for row in decision if row.get("status") == _STATUS_BUY]
    blocked = [row for row in decision if row.get("status") == _STATUS_BLOCKED]
    mature = [row for row in buy_days if (row.get("outcome") or {}).get("status") == "MATURE"]

    def column(name: str) -> list[float]:
        return [value for row in mature
                if (value := (row.get("outcome") or {}).get(name)) is not None]

    return {
        "n_rows": len(rows),
        # n_observations = 决策日计数 = 成熟门(≥MATURE_MIN_OBSERVATIONS)读的那个数。
        "n_observations": len(decision),
        "n_decision_days": len(decision),
        "n_buy_days": len(buy_days),
        "n_blocked_days": len(blocked),
        "n_no_run_days": sum(1 for row in rows if row.get("status") == _STATUS_NO_RUN),
        "action_coverage": (round(len(buy_days) / len(decision), 6) if decision else None),
        "n_contract_errors": sum(1 for row in rows if row.get("contract_errors")),
        # I-6:已登记观测拒绝被重算改写的行数。它同时计进 n_contract_errors,所以
        # registry 的 `contract_error_n ≤ 0` 守卫会立刻把实验判成不可晋升 —— 这是故意的:
        # 存在未对账的改写时,晋升证据本来就不该被采信。
        "n_frozen_observations": sum(1 for row in rows if row.get("superseded")),
        # rel 两列因缺 `industry` 算不出来、但绝对主尺已成熟的行(M-13);不是 PENDING。
        "n_rel_degraded": sum(
            1 for row in rows
            if (row.get("outcome") or {}).get("rel_source") == "NO_INDUSTRY_COLUMN"),
        "n_mature": len(mature),
        "n_pending": sum(1 for row in buy_days
                         if (row.get("outcome") or {}).get("status") == "PENDING"),
        "n_pinned_buys": sum(1 for row in buy_days if row.get("pinned")),
        "gap_c1_o2": _stats(column("gap_c1_o2")),
        REL_MARKET: _stats(column(REL_MARKET)),
        REL_SECTOR: _stats(column(REL_SECTOR)),
    }


# ── 渲染 ───────────────────────────────────────────────────────────────────
def _pct(value: object) -> str:
    return "—" if value is None else f"{float(value):+.2%}"


def _num(value: object, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _faces_cell(row: dict) -> str:
    faces = row.get("faces") or {}
    if not faces:
        return "—"
    return "/".join(_num(faces.get(face), 2) for face in _FACES)


def _legacy_block(legacy: dict | None) -> list[str]:
    """旧 OW 基率:**分列并置**,并把定义断层写在表下面(不是脚注,是正文)。"""
    lines = ["", "## 旧 OW 基率(legacy · 定义断层 · 与上表并置)", ""]
    if not legacy:
        return lines + ["_旧买单账当前无 ≥Overweight 行(或不可读)——presence-gated,不补零_",
                        "", "> **定义断层**:旧账与本账**不连成一条趋势线**。"]
    return lines + [
        "| 口径 | n | 已实现 | 胜率 | 均值 |",
        "|---|---:|---:|---:|---:|",
        f"| 旧 OW(绝对门 ≥Overweight) | n={legacy.get('n')} | "
        f"{legacy.get('n_realized')} | "
        f"{'—' if legacy.get('win2') is None else format(legacy['win2'], '.0%')} | "
        f"{_pct(legacy.get('mean2'))} |",
        "",
        "> **定义断层**:旧账与本账**不连成一条趋势线**。两处不同 —— ①决策对象(绝对"
        "「值得买」 vs 相对「最值得买」);②人口(≥OW 的卡 vs 当日全部 L4 候选的相对冠军)。",
        "",
        "> **尺是同一把**(勘误,2026-08-09 复核 M-5):本行此前称「③尺不同(旧行 `fwd_2` "
        "在 T16 换尺前后混口径)」—— **不成立**。`buy_ledger.roll()` 的 `fwd_2` 列是"
        "`_a(MAIN_RULER)` 每次现算(`buy_ledger.py:168`,该账无持久化 CSV),三条已实现"
        f"旧行与本账同为 `{REL_GAP_RULER}`。所以两账**可比但不可续**:同一把尺,量的是"
        "两个不同的决策问题 —— 不连线的理由是 ① 和 ②,不是尺。",
    ]


def render(rows: list[dict], *, legacy_ow: dict | None = None) -> str:
    summary = summarize(rows)
    coverage = summary["action_coverage"]
    lines = [
        "# 相对 BUY 影子账本(basis=relative;**影子**,不写生产 buy ledger)",
        "",
        f"> 尺:绝对 `{REL_GAP_RULER}` = {RULER_WINDOW}(T+1 收盘买 → T+2 开盘卖);"
        f"相对 `{REL_MARKET}`(全市场可交易等权超额,主)/ `{REL_SECTOR}`"
        f"(申万一级可交易等权超额,辅)。三尺同屏,互不替代。",
        "",
        "> 语义:系统对外只有一个 `BUY`,最低那只是**相对 BUY**——「今日可交易全集里相对"
        "最值得买」,**不承诺绝对上涨**。",
        "",
        "## 行动覆盖(action coverage)",
        "",
        f"- 决策日 n={summary['n_decision_days']}"
        f"(BUY {summary['n_buy_days']} · BLOCKED {summary['n_blocked_days']});"
        f"NO_RUN {summary['n_no_run_days']} 日不计入分母",
        f"- action coverage = {summary['n_buy_days']}/{summary['n_decision_days']} = "
        + ("—" if coverage is None else f"{coverage:.0%}"),
        f"- 契约错 {summary['n_contract_errors']} 条 · 成熟观测 n={summary['n_mature']} · "
        f"待成熟 {summary['n_pending']} · 影子 BUY 落在持仓(📌)上 "
        f"{summary['n_pinned_buys']} 次",
    ]
    if summary["n_frozen_observations"]:
        lines.append(
            f"- 🧊 **已冻结观测 {summary['n_frozen_observations']} 条**:重算结果与已登记的"
            "那条不是同一个决策,账本**保留已登记值**、把新结果只记进 `superseded`"
            "(已汇集的观测不可被悄悄改写)。这些行同时计进契约错 —— 未对账前实验不可晋升。")
    if summary["n_mature"] < MATURE_MIN_OBSERVATIONS:
        lines += [
            f"- ⚠️ **IMMATURE**:成熟观测 n={summary['n_mature']} < "
            f"{MATURE_MIN_OBSERVATIONS} 个决策日,读数只作认知底片,**不外推**。"
            f"成熟门自**首条观测**起算,不是从预注册日起算。",
        ]
    if summary["n_contract_errors"]:
        lines.append("")
        lines.append("### ⚠️ 契约违约")
        lines.append("")
        for row in rows:
            for error in row.get("contract_errors") or []:
                lines.append(f"- `{row.get('date')}`:{error}")

    lines += [
        "",
        "## 逐日",
        "",
        "| 日期 | 状态 | 影子 BUY | 评级 | 📌 | score | 四面(目标/召回/证据/风险) | "
        f"`{REL_GAP_RULER}` | `{REL_MARKET}` | `{REL_SECTOR}` | 成熟 | 备注 |",
        "|---|---|---|---|---|---:|---|---:|---:|---:|---|---|",
    ]
    weak_days: list[str] = []
    for row in rows:
        outcome = row.get("outcome") or {}
        gap = outcome.get("gap_c1_o2")
        note = ""
        if gap is not None and gap < 0:
            note = WEAK_MARKET_NOTE
            weak_days.append(str(row.get("date")))
        elif row.get("status") == _STATUS_BLOCKED:
            note = "、".join(f"{item.get('reason')}×{item.get('n')}"
                            for item in (row.get("blocked_reasons") or [])) or "—"
        elif row.get("status") == _STATUS_NO_RUN:
            note = "当日未跑 scan(不计入分母)"
        pick = (f"{row.get('name') or '—'}({row.get('code')})" if row.get("code") else "—")
        lines.append(
            f"| {row.get('date')} | {row.get('status')} | {pick} | "
            f"{row.get('research_rating') or '—'} | {'📌' if row.get('pinned') else ''} | "
            f"{_num(row.get('relative_decision_score'))} | {_faces_cell(row)} | "
            f"{_pct(gap)} | {_pct(outcome.get(REL_MARKET))} | "
            f"{_pct(outcome.get(REL_SECTOR))} | {outcome.get('status', '—')} | {note} |")

    lines += ["", "## 三尺同屏(只统计成熟行)", "",
              "| 尺 | 分子 | 分母 | as-of | n | 均值 | 中位 | 左尾(最差) | 负行数 |",
              "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for column, numerator, denominator in (
        ("gap_c1_o2", "影子 BUY 当日 `gap_c1_o2`", "—(绝对尺,无分母)"),
        (REL_MARKET, "同上", "当日全市场可交易(`entry_tradable`)等权均值"),
        (REL_SECTOR, "同上", "同申万一级可交易等权均值"),
    ):
        stats = summary[column]
        lines.append(
            f"| `{column}` | {numerator} | {denominator} | `{RULER_WINDOW}` | "
            f"{stats['n']} | {_pct(stats['mean'])} | {_pct(stats['median'])} | "
            f"{_pct(stats['worst'])} | {stats['n_negative']} |")
    lines += [
        "",
        f"- 逐日分母读数(市场等权均值 / 分母 n)见账本 `outcome.market_mean_gap` / "
        f"`outcome.benchmark_n`;as-of = 该分析日的 {RULER_WINDOW},源 "
        "`context/scan/<date>/retro/attribution.csv`。",
    ]
    if summary["n_rel_degraded"]:
        lines += [
            "",
            f"- ⚠️ {summary['n_rel_degraded']} 行的 attribution 缺 `industry` 列 → 相对两列"
            "算不出来(`outcome.rel_source = NO_INDUSTRY_COLUMN`),但绝对 "
            f"`{REL_GAP_RULER}` 已成熟、照常入表。**这不是 PENDING**:再等一天也不会有,"
            "要补的是行业列(M-13)。",
        ]
    if weak_days:
        lines += [
            "",
            f"- ⚠️ 绝对 gap 为负的日子({len(weak_days)}):{'、'.join(weak_days)}"
            f" —— {WEAK_MARKET_NOTE}。相对 BUY 的承诺是「当日全集里相对最优」,"
            "弱市里它可以既是当日最优、又是绝对亏损;这两件事同时为真,不得互相改写。",
        ]
    lines += _legacy_block(legacy_ow)
    lines += [
        "",
        "## 诚实局限",
        "",
        "- 本账全部行 `mode=shadow`:**不写生产 buy ledger、不改 publisher、不碰 "
        "`decision_records.json`**。活体切换是独立 GATED task(≥"
        f"{MATURE_MIN_OBSERVATIONS} 个决策日 + 五守卫 + 人批),登记在 "
        "`experiment_registry` 的 `exp_relative_buy_owner`。",
        "- `PENDING` = 主尺尚未成熟(需 T+2 开盘)或该日 retro 归因未跑,**不是 0**。",
        "- 两个「基准 n」**不可互换**:决策文档的 `benchmark.market_n` 是当日 **L0 可交易"
        "全集**(finalizer 自己算四面分位用的分母);`outcome.benchmark_n` 才是 "
        f"`{REL_MARKET}` 真正的分母 = **全市场可交易**(T22 I-1 人口裁定,含漏在 L0 的"
        "票)。两数常年不等(2026-08-04 实测 4193 vs 5426),引用时必须点名是哪一个。",
        "- 影子 BUY 若落在已持仓(📌)票上,读数会与「新开仓」混在一起;逐行 `pinned` 已"
        "留痕,分层统计自取(retro 的 L3 edge 曾被📌保送污染,同族前科)。",
    ]
    return "\n".join(lines) + "\n"


def write_report(rows: list[dict], path: Path | str | None = None,
                 *, legacy_ow: dict | None = None) -> Path:
    target = Path(path or REPORT_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(render(rows, legacy_ow=legacy_ow), encoding="utf-8")
    temp.replace(target)
    return target


def legacy_ow_base_rate(scan_root: Path | str | None = None) -> dict | None:
    """旧 OW 基率(`buy_ledger` 的单一事实源;读不到 → None,不硬编码历史数字)。"""
    try:
        from autoresearch.learning.buy_ledger import rating_base_rates, roll as buy_roll

        rates = rating_base_rates(buy_roll(Path(scan_root or SCAN_ROOT)))
    except Exception as exc:  # noqa: BLE001 — 旧账读不到不该拖垮新账
        print(f"[relative_ledger] 旧 OW 基率跳过: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None
    for rate in rates:
        if rate.get("rating") in ("Overweight", "Buy"):
            return rate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="相对 BUY 影子账本(确定性,零 LLM,零联网)")
    parser.add_argument("--scan-root", default=None)
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--no-replay", action="store_true",
                        help="只读已有决策文件,不回放缺决策文件的历史日")
    args = parser.parse_args(argv or [])
    scan = Path(args.scan_root) if args.scan_root else SCAN_ROOT
    ledger = Path(args.ledger) if args.ledger else LEDGER_PATH
    report = Path(args.report) if args.report else REPORT_PATH
    rows = roll(scan, ledger, replay_missing=not args.no_replay)
    write_report(rows, report, legacy_ow=legacy_ow_base_rate(scan))
    summary = summarize(rows)
    print(f"[relative_ledger] {summary['n_rows']} 行(决策日 "
          f"{summary['n_decision_days']} · BUY {summary['n_buy_days']} · BLOCKED "
          f"{summary['n_blocked_days']} · 成熟 {summary['n_mature']} · 契约错 "
          f"{summary['n_contract_errors']})→ {ledger} / {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
