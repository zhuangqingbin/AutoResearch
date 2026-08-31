#!/usr/bin/env python3
"""stock-research 结果账本 —— 独立产物(full 报告 / lite 决策卡)事后到底涨没涨
(确定性、零 LLM、零网络、**只记不学**)。

design: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-8-brief.md`(D5.1)

## 病灶

`stock-research`(full 全量报告 / lite 决策卡)是**独立跑**的产物——不像 scan-market
的推荐票那样落进一次 run 的 staging、被 `scan.outcome` 记账。研究做完就蒸发:没人回答
「这份 full 报告 / 这张 lite 卡后来评级对不对」。本模块补的正是这条腿,镜像
`scan.outcome` 的判据(输入天天在产,缺的是记录者):

- **不**回注任何 prompt、**不**改任何权重/评级、**不**产生 proposal;
- 只有两个动词:`ingest`(发现新产物,记下当时的评级/proposal)与 `fill`(D+2 成熟后
  补主尺 `gap_c1_o2`;full 报告的长持仓窗另补 `fwd_5/10/20`)。

## 两个 tier 的产物形状不同,读法也不同

- **full**(`analyze.assemble` 产出):`$RPT/analyze/<run_dir>/manifest.json` 已经是
  v2 schema(含 `engine`/`rating`/`proposal`,assemble.py 写出前已用
  `parse_rating(strict=True)` 硬校验过)——账本直接吃这些字段,不解析报告正文。
  **2026-08-31 之前的历史产物是 v1**(没有这几个键):`.get()` 兜底,视作缺契约
  (`contract_ok=false`),不当场炸、不假装有评级。
- **lite**(独立跑的决策卡,`$RPT/analyze/<run_dir>/<名称|TICKER>_lite.md`):没有
  manifest,评级/代码/名称/日期都要从卡片正文解析——`parse_rating(strict=True)` 拿
  评级(缺→None,不兜底猜 Hold),`FINAL TRANSACTION PROPOSAL` 行拿 proposal(与
  `analyze.assemble` 校验 full 报告用的同一个正则),头一行 `# 决策卡 — <代码> <名称>
  @ <date>`(lite-playbook.md 两张卡模板共用的头)拿代码/名称/分析日。

情景概率(`ev_pct`/`scenario_p_bull/base/bear`)要等 P2 的 D4.9 才有机器可读格式——
本次只把列立好,**不改卡模板**、不解析,永远留空。

## 落在哪

    $RPT/analyze/_ledger/cards.csv   # 跨 run 索引,幂等 upsert(镜像 scan 的 ledger 形状;
                                      # `contracts/artifacts.py` 已登记 analyze_ledger_cards)

幂等键 `(run_dir, file)`。**`ingest` 只增不改**——已存在的键永远原样保留,绝不用新
解析结果覆盖旧行:`fill` 写进去的事后列(`gap_c1_o2`/`fwd_5/10/20`/`matured`)如果被
`ingest` 覆盖清空,等于把算好的战绩当场抹掉。`fill` 反过来只碰 `matured != true` 的行
(增量,成本只与「还没成熟的」成正比,同 `scan.outcome.fill` 的既定写法)。

写法照抄 `autoresearch.scan.outcome`(`grep -n "LEDGER_CSV" autoresearch/scan/outcome.py`
或 `grep -n upsert`):读全量 CSV → 内存 dict 合并 → `csv.DictWriter` 整份重写(不引
portalocker 等新依赖;这就是本仓账本类 CSV 的既定"原子写"写法,JSON 才用 tmp+replace)。

## 用法

    uv run --no-sync python -m autoresearch.analyze.ledger ingest
    uv run --no-sync python -m autoresearch.analyze.ledger fill
    uv run --no-sync python -m autoresearch.analyze.ledger nightly   # = ingest + fill 连跑
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.analyze.assemble import _is_ashare
from autoresearch.common import ruler as _ruler, workspace as ws

LEDGER_DIRNAME = "_ledger"
LEDGER_CSV = "cards.csv"

#: 列序即 CSV 表头。`contract_ok` 不在 brief 枚举的 20 列字面列表里,但紧跟在其后的一句
#: 明写「缺→行留空+`contract_ok=false` 列」——两处合读,列必须存在,放在 rating/proposal
#: 后面最自然(它就是那两列有没有解析成功的旗)。
LEDGER_COLUMNS = (
    "ingested_at", "run_dir", "file", "ticker", "name", "market", "tier",
    "analysis_date", "engine", "rating", "proposal", "contract_ok",
    # 情景概率(P2 的 D4.9 才有机器可读格式)——列先立好,先全空,不解析、不改卡模板。
    "ev_pct", "scenario_p_bull", "scenario_p_base", "scenario_p_bear",
    # 事后读数(`fill` 写):主尺 D+2 成熟才算;fwd_5/10/20 只给 full 报告补。
    # `maturity_status` 是修复轮 1(reviewer Important-1)加的:`matured` 单独一个
    # 布尔盖不住「永久不可测」与「还没到期」这两件语义完全不同的事,见 MATURITY_STATUSES。
    "gap_c1_o2", "fwd_5", "fwd_10", "fwd_20", "maturity_status", "matured",
)

#: `maturity_status` 闭集(D5.1 修复轮 1)。本仓铁律「『没测到』和『测出来是零』是两件事」
#: 在这里的具体化:下面五个值里,只有一个是"会自己解决"的临时态,其余四个都是"永远
#: 不会再变"的终态——读账本的人必须能分清「这行明天可能就好了」和「这行永远不会有
#: 读数」,不能只靠一个 `matured=false` 猜。
MATURITY_STATUSES = (
    "MATURED",              # gap_c1_o2 算出来了(matured=true 与此同义)
    "PENDING_D2",           # analysis_date 是有效交易日,但 D+2(或那两天的行情文件本身)
                             # 还没落湖 —— 明天/下次 fill 会自己重试,不是缺陷
    "NA_NON_TRADING_DAY",   # analysis_date 本身不在湖的交易日历里(含空日期)——A 股
                             # 周末/节假日永远不会有这天的行情,永久不可测
    "NA_MARKET",            # ticker 解析不出 6 位 A 股代码(如美股票)——湖只装 A 股行情,
                             # 永久不可测
    "NA_NO_LAKE_ROW",       # 交易日有效、代码形状也对,但这只票在整段前瞻窗口
                             # (D+1..D+20)湖里一行都找不到,或恰好缺 D+1/D+2 那两天的
                             # 行情(停牌/代码有误/尚未上市)——同样永久不可测
)


# ───────────────────────── 落点 ─────────────────────────

def _analyze_root(reports_root: Path | None = None) -> Path:
    return Path(reports_root) if reports_root is not None else (ws.reports_root() / "analyze")


def ledger_root(reports_root: Path | None = None) -> Path:
    return _analyze_root(reports_root) / LEDGER_DIRNAME


def _cards_path(reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / LEDGER_CSV


# ───────────────────────── 读盘:manifest.json / *_lite.md → 一行 ─────────────────────────

#: lite-playbook.md 两张卡模板共用的头:`# 决策卡 — <代码> <名称> @ <date>`
#: (早停卡在同一行末尾还带一段 `〔早停…〕` 后缀,不影响这里只取到日期为止的匹配)。
_HEADER_RE = re.compile(
    r"^#\s*决策卡\s*[—-]\s*(?P<code>\S+)\s+(?P<name>\S+)\s*@\s*(?P<date>\d{4}-\d{2}-\d{2})",
    re.MULTILINE)
#: 与 `analyze.assemble` 校验 full 报告用的同一个正则(逐字同源,不另造第二份契约)。
_PROPOSAL_RE = re.compile(r"FINAL TRANSACTION PROPOSAL:\s*\*\*(BUY|HOLD|SELL)\*\*")


def _blank_row(*, run_dir: str, file: str, tier: str, ticker: str, name: str,
               market: str, analysis_date: str, engine: str, rating: str | None,
               proposal: str | None, now: str | None) -> dict:
    return {
        "ingested_at": now or "",
        "run_dir": run_dir, "file": file,
        "ticker": ticker, "name": name, "market": market, "tier": tier,
        "analysis_date": analysis_date, "engine": engine,
        "rating": rating or "", "proposal": proposal or "",
        "contract_ok": "true" if (rating and proposal) else "false",
        "ev_pct": "", "scenario_p_bull": "", "scenario_p_base": "", "scenario_p_bear": "",
        "gap_c1_o2": "", "fwd_5": "", "fwd_10": "", "fwd_20": "",
        "maturity_status": "", "matured": "false",
    }


def _row_from_manifest(run_dir: Path, manifest_path: Path, *, now: str | None) -> dict | None:
    with contextlib.suppress(OSError, UnicodeDecodeError):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(manifest, dict):
            return None
        rating = manifest.get("rating")
        proposal = manifest.get("proposal")
        # v1 历史产物(2026-08-31 前)没有 "engine" 键;该目录只可能是本引擎自己写的
        # ——双引擎隔离下 reports_<engine>/ 从不跨引擎共享(`common.workspace` docstring)。
        engine = str(manifest.get("engine") or ws.ENGINE)
        return _blank_row(
            run_dir=run_dir.name, file=manifest_path.name, tier="full",
            ticker=str(manifest.get("ticker") or ""),
            name=str(manifest.get("name") or ""),
            market=str(manifest.get("market") or ""),
            analysis_date=str(manifest.get("analysis_date") or ""),
            engine=engine,
            rating=str(rating) if rating else None,
            proposal=str(proposal) if proposal else None,
            now=now)
    return None


def _row_from_lite(run_dir: Path, lite_path: Path, *, now: str | None) -> dict | None:
    with contextlib.suppress(OSError, UnicodeDecodeError):
        text = lite_path.read_text(encoding="utf-8")
        m = _HEADER_RE.search(text)
        ticker = m.group("code") if m else ""
        name = m.group("name") if m else ""
        date = m.group("date") if m else ""
        rating = parse_rating(text, strict=True)          # 缺→None,不兜底猜 Hold
        proposal_m = _PROPOSAL_RE.search(text)
        proposal = proposal_m.group(1) if proposal_m else None
        return _blank_row(
            run_dir=run_dir.name, file=lite_path.name, tier="lite",
            ticker=ticker, name=name,
            market=("A股" if _is_ashare(ticker) else "其他") if ticker else "",
            analysis_date=date, engine=ws.ENGINE,
            rating=rating, proposal=proposal, now=now)
    return None


# ───────────────────────── 落盘:读全量 → 内存合并 → 整份重写(照抄 outcome.py) ─────────────────────────

def _read_cards(path: Path) -> dict[tuple[str, str], dict]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {(r.get("run_dir", ""), r.get("file", "")): r for r in csv.DictReader(fh)}


def _write_cards(path: Path, rows: dict[tuple[str, str], dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(LEDGER_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for key in sorted(rows):
            w.writerow({k: rows[key].get(k, "") for k in LEDGER_COLUMNS})


# ───────────────────────── ingest:发现新产物(只增不改) ─────────────────────────

def ingest(*, reports_root: Path | None = None, now: str | None = None) -> dict:
    """扫 `$RPT/analyze/*/{manifest.json,*_lite.md}` → 幂等 upsert 进 `cards.csv`。

    幂等键 `(run_dir, file)`;**已存在的键原样保留**,不会用重新解析的结果覆盖它
    ——否则 `fill` 已经写进去的事后列会被这里的空白值悄悄清空。
    """
    root = _analyze_root(reports_root)
    existing = _read_cards(_cards_path(reports_root))
    scanned = added = 0
    if root.is_dir():
        for run_dir in sorted(p for p in root.iterdir()
                              if p.is_dir() and not p.name.startswith("_")):
            manifest_path = run_dir / "manifest.json"
            if manifest_path.is_file():
                scanned += 1
                row = _row_from_manifest(run_dir, manifest_path, now=now)
                key = (run_dir.name, manifest_path.name)
                if row is not None and key not in existing:
                    existing[key] = row
                    added += 1
            for lite_path in sorted(run_dir.glob("*_lite.md")):
                scanned += 1
                row = _row_from_lite(run_dir, lite_path, now=now)
                key = (run_dir.name, lite_path.name)
                if row is not None and key not in existing:
                    existing[key] = row
                    added += 1
    _write_cards(_cards_path(reports_root), existing)
    return {"scanned": scanned, "added": added}


# ───────────────────────── fill:D+2 成熟后补事后列 ─────────────────────────

def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 6)


_CODE_RE = re.compile(r"\d{6}")


def _code6(ticker: object) -> str:
    m = _CODE_RE.match(str(ticker or "").strip())
    return m.group(0) if m else ""


#: `analyze` 在 A7 分层守卫(`tests/contracts/test_layering.py`)里排在 `scan`/`research`
#: **下面**,不许 import 它们两个(即便是函数体内的惰性 import——守卫按 AST 逐节点扫,
#: 不分是否延迟)。`research.factor_lab.forward_returns`/`scan.outcome.market_frame`
#: 正好都在那两层,所以这里**不复用**它们,改成直接读同一份 `lake/daily/*.parquet`
#: 按同一公式重算——公式与 `common.ruler`/`factor_lab.py` 文档逐字同源(`gap_c1_o2` =
#: `open[D+2]/close[D+1]−1`,`fwd_N` = `close[D+N]/open[D+1]−1`),不是又造一套口径,
#: 只是分层边界不许把那份实现原样 import 进来。也因此不复刻 `market_frame` 的完整截面
#: 机器(资格过滤/相对市场基准等 E6 概念)——账本记的是单票事后读数,不需要那些。
def _lake_trade_days(lake_daily: Path | None = None) -> list[str]:
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    return sorted(p.stem[:8] for p in d.glob("*.parquet") if p.stem[:8].isdigit())


def _market_returns(analysis_date: str, *,
                     lake_daily: Path | None = None) -> tuple[pd.DataFrame | None, str]:
    """分析日 → `(前向收益帧, status)`。

    帧 index=6 位代码,列 `gap_c1_o2`/`fwd_5_oc`/`fwd_10_oc`/`fwd_20_oc`。帧非 `None`
    时 `status="MATURED"`;帧是 `None` 时 `status` 是 `MATURITY_STATUSES` 里的
    `"NA_NON_TRADING_DAY"`(永久:`analysis_date` 本身不在湖的交易日历里)或
    `"PENDING_D2"`(暂时:是有效交易日,但 D+2 那两天的行情——或干脆文件本身——
    还没落湖)。返回值直接复用闭集词汇,`fill()` 不必再做第二次翻译。**测试的
    monkeypatch 点**——`fill` 不直接碰湖,一律经这一个函数。
    """
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    P = _lake_trade_days(lake_daily)
    D = str(analysis_date).replace("-", "")
    if D not in P:
        return None, "NA_NON_TRADING_DAY"
    idx = P.index(D)
    if idx + 2 >= len(P):
        return None, "PENDING_D2"                       # D+2 尚未落湖:未成熟,不是故障
    window = P[idx + 1: min(len(P), idx + 21)]           # D+1..D+20(不足 20 日就取到湖尾)
    frames = []
    for k, day in enumerate(window, start=1):
        fp = d / f"{day}.parquet"
        if not fp.is_file():
            continue
        try:
            bars = pd.read_parquet(fp, columns=["ts_code", "open", "close"])
        except (OSError, ValueError):
            continue
        if bars.empty:
            continue
        frames.append(pd.DataFrame({
            "code": bars["ts_code"].astype(str).str[:6].str.zfill(6), "k": k,
            "open": pd.to_numeric(bars["open"], errors="coerce"),
            "close": pd.to_numeric(bars["close"], errors="coerce"),
        }))
    if not frames:
        return None, "PENDING_D2"
    long = pd.concat(frames, ignore_index=True)
    o = long.pivot_table(index="code", columns="k", values="open")
    c = long.pivot_table(index="code", columns="k", values="close")
    if 1 not in o.columns or 1 not in c.columns or 2 not in o.columns:
        return None, "PENDING_D2"                       # D+1/D+2 的 open 缺席:同样是未成熟
    out = pd.DataFrame(index=o.index)
    out[_ruler.MAIN_RULER] = o[2] / c[1] - 1.0            # T+1 收买 → T+2 开卖(隔夜主尺)
    for n in (5, 10, 20):
        out[f"fwd_{n}_oc"] = (c[n] / o[1] - 1.0) if n in c.columns else np.nan
    return out, "MATURED"


def fill(*, reports_root: Path | None = None, now: str | None = None,
         lake_daily: Path | None = None) -> dict:
    """对已 ingest 的行按 `ruler.MAIN_RULER`(`gap_c1_o2`)补事后列;D+2 成熟才算。

    只碰 `matured != true` 的行(增量,同 `scan.outcome.fill` 的既定写法:成本只与
    「还没成熟的」成正比,不随账本历史长度增长)。full 报告另补 `fwd_5/10/20`;
    lite 卡这三列永远留空(隔夜口径的短窗卡,长持仓窗读数无意义)。

    每一条不成熟的行都会写一个 `MATURITY_STATUSES` 闭集里的具体原因(D5.1 修复轮 1;
    reviewer Important-1)——不再把「非交易日」「非 A 股」「湖里没这只票」「D+2 还没
    落湖」四件语义不同的事压成同一个 `matured=false`。返回值里的 `skipped_by_reason`
    是同一批原因的计数,`skipped` 仍是总数(向后兼容)。
    """
    path = _cards_path(reports_root)
    existing = _read_cards(path)
    filled = skipped = 0
    reason_counts: dict[str, int] = {}
    cache: dict[str, tuple[pd.DataFrame | None, str]] = {}

    def _mark_unmatured(row: dict, status: str) -> None:
        nonlocal skipped
        row["maturity_status"] = status
        row["matured"] = "false"
        reason_counts[status] = reason_counts.get(status, 0) + 1
        skipped += 1

    for row in existing.values():
        if str(row.get("matured", "")).lower() == "true":
            continue
        code = _code6(row.get("ticker"))
        if not code:
            _mark_unmatured(row, "NA_MARKET")            # 不是能解析出的 6 位 A 股代码
            continue
        date = str(row.get("analysis_date") or "")
        if not date:
            _mark_unmatured(row, "NA_NON_TRADING_DAY")   # 连日期都没有,视同无效交易日
            continue
        if date not in cache:
            cache[date] = _market_returns(date, lake_daily=lake_daily)
        fr, status = cache[date]
        if fr is None:
            _mark_unmatured(row, status)                 # 已是 "NA_NON_TRADING_DAY"/"PENDING_D2"
            continue
        if code not in fr.index:
            _mark_unmatured(row, "NA_NO_LAKE_ROW")       # 整段窗口湖里都没有这只票
            continue
        gap = _num(fr.loc[code].get(_ruler.MAIN_RULER))
        if gap is None:
            _mark_unmatured(row, "NA_NO_LAKE_ROW")       # D+1/D+2 恰好缺它的行情(停牌等)
            continue
        row["gap_c1_o2"] = gap
        row["maturity_status"] = "MATURED"
        row["matured"] = "true"
        if row.get("tier") == "full":
            row["fwd_5"] = _num(fr.loc[code].get("fwd_5_oc"))
            row["fwd_10"] = _num(fr.loc[code].get("fwd_10_oc"))
            row["fwd_20"] = _num(fr.loc[code].get("fwd_20_oc"))
        filled += 1
    _write_cards(path, existing)
    return {"filled": filled, "skipped": skipped,
            "skipped_by_reason": dict(sorted(reason_counts.items()))}


# ───────────────────────── CLI ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="stock-research 结果账本(确定性、只记不学)")
    ap.add_argument("command", choices=["ingest", "fill", "nightly"])
    ap.add_argument("--today", default=None, help="ingested_at/事后列计算时刻(留痕用)")
    args = ap.parse_args(argv)
    out: dict = {"ok": True}
    if args.command in ("ingest", "nightly"):
        out["ingest"] = ingest(now=args.today)
    if args.command in ("fill", "nightly"):
        out["fill"] = fill(now=args.today)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
