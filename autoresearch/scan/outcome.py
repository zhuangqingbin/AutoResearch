#!/usr/bin/env python3
"""结果账本 —— 一只推荐票事后到底涨没涨(确定性、零 LLM、零网络、**只记不学**)。

design: docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md §4.4

## 病灶

2026-08-21「整个 learning 层退役」删掉了 `relative_ledger` / `buy_ledger` / `retro` —— 那些
是**从历史里学、回注 prompt、自动提案**的腿,删得对。但它们顺手带走了唯一一件**记录**:
从此没有任何东西回答「这只推荐票后来怎么样了」。E6 转正设计稿的 U6 写着「转正后的记分册 =
relative_buy 账本本身」,而那本账已经没有实体。用户第 ① 件事(「用于复盘当时的推荐股票
链路」)缺的正是这条腿。

**判据仍然是那一条**:输入没人生产就不留 —— 这次输入(推荐本身)天天在产,缺的是记录者。
所以本模块只有「记」:

- **不**回注任何 prompt、**不**改任何权重/门/评级、**不**产生 proposal;
- 消费者只有两个:`chain_view` 的 ⑩ 结果段,与 prelude 汇总屏一行读数;
- **不进 brief、不喂任何 agent**(同 `l4_rejection` 日读的边界)。

## 口径(与 `research.edge_census` 逐字同源)

前向收益走 `factor_lab.forward_returns`(同一实现)、可交易折叠走 `ruler.entry_tradable`、
`|gap| > GAP_CLIP` 视作数据错置 NaN —— 两者读数因此可以直接对表。三个相对列的分母各不相同,
**列名即口径**,不要混读:

| 列 | 口径 |
|---|---|
| `rel_gap_market` | gap − 当日全湖可交易**等权均值**(`ruler.REL_MARKET`,E6 口径) |
| `rel_gap_sector` | gap − 同行业可交易**等权均值**(`ruler.REL_SECTOR`) |
| `excess_med_market` | gap − 当日全湖可交易**中位**(`edge_census` 主列口径) |

`exec_ok` = 设计稿 A4 的 T+1 收盘执行条件(当日涨幅 ≤3% ∧ 收盘不在当日区间上 30% ∧ 未封
涨停)。**它是量尺不是门**:即便 A4 尚未上线,先把它记下来,才有「BUY 无条件」与
「BUY ∧ 执行条件满足」两条曲线可比。证据见设计稿 §1.5/§1.6(四年全湖逐年同号)。

## 落在哪(与设计稿的一处偏离,实施时决定)

设计稿写的是 `trace/outcome.json`(run 目录内)。**实施时改到 `_ledger/` 下**,因为同一波
刚刚给 run 目录立了「发布后不再变」的 MANIFEST 不变量 —— 事后往 run 里写文件会让每个 run
的 `verify` 永远报一条 `extra`,等于自己把刚立的哨兵弄哑。现在:

    reports_<engine>/scan/_ledger/outcome/<run_id>.json     # 逐 run 明细
    reports_<engine>/scan/_ledger/recommendations.csv       # 跨 run 索引(按 run_id,code 幂等 upsert)

  uv run --no-sync python -m autoresearch.scan.outcome fill [--today 2026-08-26] [--limit 5]
  uv run --no-sync python -m autoresearch.scan.outcome line
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.scan.run_naming import is_run_dir

OUTCOME_SCHEMA_VERSION = 1
LEDGER_DIRNAME = "_ledger"
LEDGER_CSV = "recommendations.csv"
MAIN = _ruler.MAIN_RULER

#: A4 执行线阈值(设计稿 §3 路A)。**先量后用**:上线与否是产品裁定,这里只负责记下
#: 「若按此执行会怎样」。证据:四年全湖 1086 日,收在当日区间上 30% 的票隔夜比全体差
#: 0.13~0.27pp,**逐年同号**(2022–2026 无一年反号)。
#:
#: D8.3⑤:数值真身搬去 `contracts.agent_output`(两份手写文档与这里各写一份、没有
#: 测试对齐的病灶登记在那边的模块 docstring)。这里的两个名字**引用同一个对象**
#: (`is` 恒成立),不是复制——改数值只需要改那一处。
EXEC_MAX_PCT_1D = EXEC_LINE_MAX_PCT_1D
EXEC_MAX_POS_IN_RANGE = EXEC_LINE_MAX_POS_IN_RANGE

LEDGER_COLUMNS = (
    # `mode`/`src` 是**读这本账之前必须先看的两列**,不是装饰:
    #  - `mode`  shadow 期的 BUY 在报告里明写「非正式·不执行」,与 active 期的 BUY 不是同一件事;
    #  - `src`   `shared` = 这一行读自共享 staging(同数据日重跑会覆盖),未必是本 run 当时那份。
    #    实测 2026-08-13/08-18 两天:brief 印的是 BLOCKED,而共享 staging 的决策文件被后来的
    #    影子回放改写成 `buys=[688766]`(还是一只 📌 持仓)。不分列读 = 把两条假 BUY 算进战绩。
    "run_id", "analysis_date", "mode", "src", "code", "name", "sector", "role", "lane", "guard",
    "conviction", "rating", "proposal", "early_stop_reason", "e6_rank", "e6_eligible",
    "e6_buy", "buyable_c1", "t1_open", "t1_high", "t1_low", "t1_close", "t1_pct_chg",
    "t1_pos_in_range", "exec_ok", "t2_open", "gap_c1_o2", "rel_gap_market",
    "rel_gap_sector", "excess_med_market", "fwd_5_oc", "fwd_10_oc", "ruler",
    # ── 时间锚(2026-08-28 §2.4 G1)。**读 BUY 战绩前必须先看 `actionability`** ──
    #  `anchor_session` 是本行主尺真正的买腿日:正常 run = analysis_date 的下一交易日
    #  (与上面各列同源);迟到 run(报告在 T+1 收盘后才就绪)的那一天已经过去了,
    #  上面的 `gap_c1_o2` 记的是一笔**下不了的单**。`exec_gap_c1_o2` 是同一把尺从
    #  第一个真正来得及的尾盘起算的反事实,**绝不与上面那列混算均值**(两个人口)。
    "anchor_session", "exec_lag", "actionability", "exec_gap_c1_o2",
    "computed_at",
)


# ───────────────────────── 定位:run 目录 → 当日事实 ─────────────────────────

def ledger_root(reports_root: Path | None = None) -> Path:
    return Path(reports_root or (ws.reports_root() / "scan")) / LEDGER_DIRNAME


def _read_json(base: Path, *names: str) -> object | None:
    for name in names:
        p = base / name
        if p.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                return json.loads(p.read_text(encoding="utf-8"))
    return None


def _read_rows(base: Path, *names: str) -> list[dict]:
    for name in names:
        p = base / name
        if p.is_file():
            with contextlib.suppress(OSError, UnicodeDecodeError):
                with p.open(encoding="utf-8-sig", newline="") as fh:
                    return list(csv.DictReader(fh))
    return []


def _z6(value: object) -> str:
    return str(value or "").split(".")[0].strip().zfill(6)


def run_facts(run_dir: Path | str) -> dict:
    """一次 run 的「当天推荐了谁、判成什么」—— 只读,缺什么就少什么(不伪造)。

    读盘优先级 `trace/staging/` → `trace/` → 共享 staging;最后那级会被标记,因为同数据日
    重跑会覆盖它(实测 64 个已发布 run 只剩 49 个 staging)。
    """
    run = Path(run_dir)
    manifest = _read_json(run, "manifest.json") or {}
    date = str(manifest.get("analysis_date") or "")
    bases = [run / "trace" / "staging", run / "trace"]
    if date:
        bases.append(ws.scan_root() / date)
    used_shared = False

    def pick_rows(*names: str) -> list[dict]:
        nonlocal used_shared
        for i, base in enumerate(bases):
            rows = _read_rows(base, *names)
            if rows:
                used_shared = used_shared or (i == 2)
                return rows
        return []

    def pick_json(*names: str) -> object | None:
        nonlocal used_shared
        for i, base in enumerate(bases):
            doc = _read_json(base, *names)
            if doc is not None:
                used_shared = used_shared or (i == 2)
                return doc
        return None

    finalists = pick_rows("L3_fine_finalists.csv", "finalists.csv")
    ratings = pick_json("_final_ratings.json") or {}
    early = pick_json("_early_stop.json") or {}
    decision = pick_json("_relative_buy_decision.json") or {}
    judged = {_z6(r.get("code")): r for r in pick_rows("L3_judged_full.csv")}

    buys = {_z6(b.get("code")) for b in (decision.get("buys") or [])}
    cand = {_z6(c.get("code")): c for c in (decision.get("candidates") or [])}

    rows: dict[str, dict] = {}
    for fr in finalists:
        code = _z6(fr.get("code") or fr.get("ticker"))
        if code == "000000":
            continue
        lane = str(fr.get("lane") or "")
        guard = str(fr.get("guard") or "")
        rows[code] = {
            "code": code,
            "name": str(fr.get("name") or ""),
            "sector": str(fr.get("sector") or ""),
            "lane": lane,
            "guard": guard,
            "conviction": fr.get("conviction"),
            "role": ("pinned" if lane == "pinned" else
                     "composite_seat" if guard == "composite_seat" else "finalist"),
        }
    for code, rating in (ratings.items() if isinstance(ratings, dict) else []):
        code = _z6(code)
        rows.setdefault(code, {"code": code, "name": "", "sector": "", "lane": "",
                               "guard": "", "conviction": None, "role": "rated"})
        rows[code]["rating"] = rating
    for code, row in rows.items():
        j = judged.get(code) or {}
        row.setdefault("rating", None)
        row.setdefault("name", str(j.get("name") or ""))
        if not row.get("sector"):
            row["sector"] = str(j.get("sector") or "")
        if row.get("conviction") in (None, ""):
            row["conviction"] = j.get("conviction")
        stop = early.get(code) if isinstance(early, dict) else None
        row["early_stop_reason"] = (stop or {}).get("reason") if isinstance(stop, dict) else None
        c = cand.get(code)
        row["e6_rank"] = (c or {}).get("rank")
        row["e6_eligible"] = (c or {}).get("eligible")
        row["e6_buy"] = code in buys
        if code in buys:
            row["role"] = "BUY"
    return {"analysis_date": date, "rows": rows, "used_shared": used_shared,
            "contract_run_id": str(manifest.get("run_id") or ""),
            "decision_mode": str(decision.get("mode") or ""),
            "rule_version": str(decision.get("rule_version") or "")}


# ───────────────────────── 市场事实:湖 → 前向收益 ─────────────────────────

def market_frame(date: str, *, lake_daily: Path | None = None) -> tuple[pd.DataFrame | None, dict]:
    """当日全湖前向收益帧 + T+1 盘口(open/high/low/close/pct_chg)。

    `None` = 湖里还没有 D+2 收盘(结果尚未成熟)—— 那是**状态不是故障**,`fill` 会跳过并
    留着下次再算。口径与 `edge_census.forward_frame` 逐字同源(同一 `forward_returns`、
    同一 `GAP_CLIP` 数据错剔除)。
    """
    from autoresearch.research import edge_census as ec

    P = ec.lake_trade_days(lake_daily)
    D = str(date).replace("-", "")
    if D not in P:
        return None, {"reason": "非交易日或湖里没有该日"}
    idx = P.index(D)
    if idx + 2 >= len(P):
        return None, {"reason": "D+2 尚未落湖(结果未成熟)"}
    window = P[max(0, idx - 1): min(len(P), idx + 13)]
    piv = ec.load_lake_pivots(window, lake_daily)
    fr = ec.forward_frame(piv, P, D)
    if fr is None or fr.empty:
        return None, {"reason": "前向收益帧为空"}
    D1 = P[idx + 1]
    for col, key in (("t1_open", "open"), ("t1_high", "high"), ("t1_low", "low"),
                     ("t1_close", "close"), ("t1_pct_chg", "pct_chg")):
        series = piv.get(key)
        fr[col] = series[D1] if (series is not None and D1 in series.columns) else np.nan
    fr["t2_open"] = piv["open"][P[idx + 2]] if P[idx + 2] in piv["open"].columns else np.nan
    span = fr["t1_high"] - fr["t1_low"]
    fr["t1_pos_in_range"] = ((fr["t1_close"] - fr["t1_low"]) / span).where(span > 0)
    return fr, {"t1": D1, "t2": P[idx + 2], "n": int(len(fr))}


def _relative_columns(fr: pd.DataFrame, sectors: dict[str, str]) -> pd.DataFrame:
    """三个相对列。分母各不相同,**列名即口径**(见模块 docstring 的表)。"""
    ok = _ruler.entry_tradable(fr, ruler_name=MAIN)
    gap = pd.to_numeric(fr[MAIN], errors="coerce")
    base = gap[ok & gap.notna()]
    out = pd.DataFrame(index=fr.index)
    out[_ruler.REL_MARKET] = gap - base.mean() if len(base) else np.nan
    out["excess_med_market"] = gap - base.median() if len(base) else np.nan
    sec = pd.Series({c: sectors.get(c, "") for c in fr.index})
    sec_mean = base.groupby(sec.reindex(base.index)).mean()
    out[_ruler.REL_SECTOR] = gap - sec.map(sec_mean).where(sec.astype(str) != "")
    return out


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 6)


def exec_anchor_frame(execution: dict | None, *, lake_daily: Path | None = None):
    """迟到 run 的**反事实**帧:主尺改从第一个真正来得及的尾盘起算。

    正常 run(`exec_lag == 0`)返回 `None` —— 锚点与主帧逐字相同,再算一遍纯属浪费。
    只有报告在 T+1 收盘之后才就绪的那些 run 才有第二个锚点,而它们的主帧记的是
    一笔**下不了的单**(61 个 run 里 8 个,13%)。

    实现上取 `first_available_session` 的**前一个**交易日做 D —— `forward_returns`
    的买腿恒为 D+1,所以这样它的 D+1 正好落在第一个可执行的尾盘上,口径与主帧
    逐字同源(同一 `forward_returns`、同一 `GAP_CLIP`),不另造一把尺。
    """
    first = str((execution or {}).get("first_available_session") or "")
    if not first or not (execution or {}).get("exec_lag"):
        return None
    from autoresearch.research import edge_census as ec

    P = ec.lake_trade_days(lake_daily)
    target = first.replace("-", "")
    if target not in P:
        return None
    i = P.index(target)
    if i == 0:
        return None
    anchor = P[i - 1]
    fr, _ = market_frame(f"{anchor[:4]}-{anchor[4:6]}-{anchor[6:]}", lake_daily=lake_daily)
    return fr


def compute_outcome(run_dir: Path | str, *, lake_daily: Path | None = None) -> dict | None:
    """一次 run → 结果文档;D+2 未落湖 → `None`(未成熟,不是失败)。"""
    run = Path(run_dir)
    facts = run_facts(run)
    date = facts["analysis_date"]
    if not date or not facts["rows"]:
        return None
    fr, meta = market_frame(date, lake_daily=lake_daily)
    if fr is None:
        return None
    # 时间锚(§2.4 G1):这份报告到底什么时候才能下单。正常 run 与主帧同锚;
    # 迟到 run 另算一份反事实帧,**两者绝不混算**(列名即人口)。
    from autoresearch.scan import exec_anchor as _anchor

    execution = _anchor.read_execution(run)
    exec_fr = None
    with contextlib.suppress(Exception):
        exec_fr = exec_anchor_frame(execution, lake_daily=lake_daily)
    sectors = {code: str(row.get("sector") or "") for code, row in facts["rows"].items()}
    rel = _relative_columns(fr, sectors)
    ok_entry = _ruler.entry_tradable(fr, ruler_name=MAIN)

    rows: dict[str, dict] = {}
    for code, row in sorted(facts["rows"].items()):
        m = fr.loc[code] if code in fr.index else None
        pct1 = _num(m["t1_pct_chg"]) if m is not None else None
        pos = _num(m["t1_pos_in_range"]) if m is not None else None
        buyable = bool(ok_entry.loc[code]) if (m is not None and code in ok_entry.index) else None
        exec_ok = None
        if pct1 is not None and pos is not None and buyable is not None:
            exec_ok = bool(buyable and pct1 <= EXEC_MAX_PCT_1D and pos < EXEC_MAX_POS_IN_RANGE)
        rows[code] = {
            **{k: row.get(k) for k in ("code", "name", "sector", "role", "lane", "guard",
                                       "conviction", "rating", "early_stop_reason",
                                       "e6_rank", "e6_eligible", "e6_buy")},
            "buyable_c1": buyable,
            "t1_open": _num(m["t1_open"]) if m is not None else None,
            "t1_high": _num(m["t1_high"]) if m is not None else None,
            "t1_low": _num(m["t1_low"]) if m is not None else None,
            "t1_close": _num(m["t1_close"]) if m is not None else None,
            "t1_pct_chg": pct1,
            "t1_pos_in_range": pos,
            "exec_ok": exec_ok,
            "t2_open": _num(m["t2_open"]) if m is not None else None,
            MAIN: _num(m[MAIN]) if m is not None else None,
            _ruler.REL_MARKET: _num(rel.loc[code, _ruler.REL_MARKET]) if code in rel.index else None,
            _ruler.REL_SECTOR: _num(rel.loc[code, _ruler.REL_SECTOR]) if code in rel.index else None,
            "excess_med_market": _num(rel.loc[code, "excess_med_market"]) if code in rel.index else None,
            "fwd_5_oc": _num(m["fwd_5_oc"]) if m is not None else None,
            "fwd_10_oc": _num(m["fwd_10_oc"]) if m is not None else None,
            "exec_gap_c1_o2": (_num(exec_fr.loc[code, MAIN])
                               if exec_fr is not None and code in exec_fr.index else None),
        }
    # 「成熟」= 主尺算得出来的行占多数。少数票停牌/新股缺数是常态,不该让整份结果反复重算。
    n_scored = sum(1 for r in rows.values() if r.get(MAIN) is not None)
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "run_id": run.name,
        "contract_run_id": facts["contract_run_id"],
        "analysis_date": date,
        "ruler": MAIN,
        "t1": meta.get("t1"), "t2": meta.get("t2"),
        "decision_mode": facts["decision_mode"],
        "rule_version": facts["rule_version"],
        "read_from_shared_staging": facts["used_shared"],
        "complete": bool(rows) and n_scored >= max(1, len(rows) // 2),
        "n_rows": len(rows), "n_scored": n_scored,
        "exec_line": {"max_pct_1d": EXEC_MAX_PCT_1D,
                      "max_pos_in_range": EXEC_MAX_POS_IN_RANGE},
        "execution": execution,
        "rows": rows,
    }


# ───────────────────────── 落盘:逐 run JSON + 跨 run CSV ─────────────────────────

def outcome_path(run_id: str, reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / "outcome" / f"{run_id}.json"


def write_outcome(doc: dict, reports_root: Path | None = None) -> Path:
    p = outcome_path(doc["run_id"], reports_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def _ledger_rows(doc: dict) -> list[dict]:
    stamp = doc.get("computed_at") or ""
    anchor = doc.get("execution") or {}
    out = []
    for code, row in sorted(doc["rows"].items()):
        out.append({
            "run_id": doc["run_id"], "analysis_date": doc["analysis_date"], "code": code,
            "mode": doc.get("decision_mode") or "",
            "src": "shared" if doc.get("read_from_shared_staging") else "run",
            **{k: row.get(k) for k in LEDGER_COLUMNS
               if k not in ("run_id", "analysis_date", "mode", "src", "code",
                            "ruler", "computed_at", "anchor_session", "exec_lag",
                            "actionability")},
            "ruler": doc["ruler"],
            # 三列同源于 doc 级 execution(逐行相同):读 BUY 战绩前先看 actionability。
            "anchor_session": anchor.get("first_available_session"),
            "exec_lag": anchor.get("exec_lag"),
            "actionability": anchor.get("actionability_status"),
            "computed_at": stamp,
        })
    return out


def upsert_ledger(doc: dict, reports_root: Path | None = None) -> int:
    """按 `(run_id, code)` 幂等 upsert 进跨 run 索引 CSV;返回本次写入/更新的行数。

    幂等是硬要求:`fill` 每晚可能对同一个 run 重算(结果尚未成熟时会重试),重复 append
    会让「BUY n 笔」这种最基础的计数直接翻倍。
    """
    path = ledger_root(reports_root) / LEDGER_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple[str, str], dict] = {}
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                existing[(str(r.get("run_id", "")), _z6(r.get("code")))] = r
    fresh = _ledger_rows(doc)
    for r in fresh:
        existing[(r["run_id"], r["code"])] = r
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(LEDGER_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for key in sorted(existing):
            w.writerow({k: existing[key].get(k, "") for k in LEDGER_COLUMNS})
    return len(fresh)


def load_ledger(reports_root: Path | None = None) -> list[dict]:
    path = ledger_root(reports_root) / LEDGER_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def published_runs(reports_root: Path | None = None) -> list[Path]:
    """已发布 run 目录。两种目录名都认(新 `20260825-0826_2000` / legacy `20260826_2000`)。

    判据从「`[:2]=='20'` 且含下划线」收紧到 `run_naming.is_run_dir` —— 旧判据会把
    `_ledger`/`_failed`/`_capsule_archive` 之外任何以 20 开头带下划线的目录都当 run
    (它们只是恰好没有 `manifest.json` 才没出事)。
    """
    base = Path(reports_root or (ws.reports_root() / "scan"))
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir()
                  if p.is_dir() and is_run_dir(p.name)
                  and (p / "manifest.json").is_file())


def fill(*, reports_root: Path | None = None, lake_daily: Path | None = None,
         limit: int | None = None, now: str | None = None,
         rebuild: bool = False) -> dict:
    """回填全部**未成熟或未算过**的已发布 run。返回 `{filled, skipped, rows, runs}`。

    幂等 + 增量:已存在且 `complete` 的 run 直接跳过(不重算、不重写),所以每天跑它的
    成本只与「昨天新出的 run + 还没成熟的老 run」成正比。
    """
    filled, skipped, n_rows, touched = 0, 0, 0, []
    for run in published_runs(reports_root):
        existing = None
        p = outcome_path(run.name, reports_root)
        if p.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                existing = json.loads(p.read_text(encoding="utf-8"))
        # `rebuild`:口径变了(如 2026-08-28 新增时间锚四列)才需要重算已成熟的 run。
        # 默认关着 —— 每晚跑的成本必须只与「昨天新出的 + 还没成熟的」成正比,
        # 而不是与历史长度成正比。
        if not rebuild and isinstance(existing, dict) and existing.get("complete"):
            skipped += 1
            continue
        doc = compute_outcome(run, lake_daily=lake_daily)
        if doc is None:
            skipped += 1
            continue
        doc["computed_at"] = now or ""
        write_outcome(doc, reports_root)
        n_rows += upsert_ledger(doc, reports_root)
        filled += 1
        touched.append(run.name)
        if limit and filled >= limit:
            break
    return {"filled": filled, "skipped": skipped, "rows": n_rows, "runs": touched}


# ───────────────────────── 读数(prelude 汇总屏一行)─────────────────────────

MIN_LEDGER_N = 20


def ledger_line(reports_root: Path | None = None) -> str:
    """汇总屏一行 —— **仅人看,不喂任何 agent、不进 brief**(同 l4_rejection 日读的边界)。

    <20 笔时只印「攒样本 n/20」不印均值:小样本均值会被当成结论读,而这条线存在的意义
    正是不让人再凭印象说「最近推荐得挺准」。
    """
    rows = load_ledger(reports_root)
    if not rows:
        return "结果账本:空(还没回填过 —— `python -m autoresearch.scan.outcome fill`)"
    # 只数 **active 期**的 BUY:shadow 期的那几笔在当天报告里明写「非正式·不执行」,
    # 混进来就是把没执行过的东西算进战绩(且其中两笔的决策文件还是被影子回放改写出来的)。
    all_buys = [r for r in rows
                if str(r.get("e6_buy")).lower() == "true" and str(r.get("mode")) == "active"]
    shadow_n = sum(1 for r in rows if str(r.get("e6_buy")).lower() == "true"
                   and str(r.get("mode")) != "active")
    # 时间锚(§2.4 G1):报告在 T+1 收盘之后才就绪的那些 run,主尺买腿是**已经过去的价格**。
    # 它们不进主均值(否则 13% 的假单被算进战绩),单列一个计数如实说明被排除了几笔。
    # 老行没有这一列(账本先于本波)→ 视为未知,同样不进主均值,计入 `unknown_n`。
    buys = [r for r in all_buys if str(r.get("actionability") or "") == "ACTIONABLE"]
    late_n = sum(1 for r in all_buys
                 if str(r.get("actionability") or "") in {"LATE_REVALIDATION_REQUIRED", "EXPIRED"})
    unknown_n = len(all_buys) - len(buys) - late_n
    scored = [r for r in buys if _num(r.get(MAIN)) is not None]
    if len(scored) < MIN_LEDGER_N:
        return (f"结果账本:{len(rows)} 行 · active BUY {len(all_buys)} 笔"
                f"(可执行 {len(buys)}·已成熟 {len(scored)})"
                + (f" · 另 shadow 期 {shadow_n} 笔不计" if shadow_n else "")
                + (f" · 迟到 {late_n} 笔不计" if late_n else "")
                + (f" · 锚未知 {unknown_n} 笔不计" if unknown_n else "")
                + f" · 攒样本 {len(scored)}/{MIN_LEDGER_N},不印均值")
    gaps = [_num(r.get(MAIN)) for r in scored]
    rel = [_num(r.get(_ruler.REL_MARKET)) for r in scored
           if _num(r.get(_ruler.REL_MARKET)) is not None]
    ok = [r for r in scored if str(r.get("exec_ok")).lower() == "true"]
    ok_gaps = [_num(r.get(MAIN)) for r in ok]
    txt = (f"结果账本:BUY {len(scored)} 笔 · 均 gap {100 * float(np.mean(gaps)):+.2f}pp"
           f" · 相对市场 {100 * float(np.mean(rel)):+.2f}pp" if rel else
           f"结果账本:BUY {len(scored)} 笔 · 均 gap {100 * float(np.mean(gaps)):+.2f}pp")
    if ok_gaps:
        txt += (f" · 执行线内 {len(ok_gaps)} 笔 {100 * float(np.mean(ok_gaps)):+.2f}pp")
    if late_n or unknown_n:
        txt += f" · 排除迟到 {late_n}/锚未知 {unknown_n} 笔"
    return txt + f" · 主尺 {MAIN}(可执行口径;只记不学;仅人看)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="结果账本(确定性、只记不学)")
    ap.add_argument("command", choices=["fill", "line"])
    ap.add_argument("--limit", type=int, default=None, help="本次最多回填几个 run")
    ap.add_argument("--today", default=None, help="computed_at 时间戳(留痕用)")
    ap.add_argument("--rebuild", action="store_true",
                    help="连已成熟的 run 一起重算(口径变更后用;默认增量)")
    args = ap.parse_args(argv)
    if args.command == "line":
        print(ledger_line())
        return 0
    res = fill(limit=args.limit, now=args.today, rebuild=args.rebuild)
    print(json.dumps({"ok": True, **res}, ensure_ascii=False, sort_keys=True))
    print(ledger_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
